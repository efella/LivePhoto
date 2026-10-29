"""
Live Photo Maker - turn a short video (or a photo) into an iPhone Live Photo.

A Live Photo is two files that share a content identifier (a UUID):
  * JPEG  -> Apple MakerNote, tag 17 = content identifier
  * MOV   -> top-level metadata com.apple.quicktime.content.identifier = same UUID
  * MOV   -> timed metadata track com.apple.quicktime.still-image-time, marking the
            moment in the video that the still photo was taken from

Quality: H.264 / HEVC video is stream-copied bit-for-bit (unless you trim). The
still is the exact frame you pick, saved as a 4:4:4 quality-100 JPEG. For photo
input the original JPEG is kept untouched as the still.

Usage:
  python livephoto.py                       -> opens the app
  python livephoto.py video.mp4             -> opens the app with the video loaded
  python livephoto.py video.mp4 --cli [--still 1.5] [--start 0 --duration 3] [-o outdir]
  python livephoto.py photo.jpg --cli [--zoom 12] [-o outdir]
  add --add-to-photos (macOS) to import the result into the Photos app
"""

import argparse
import datetime
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid

APP_NAME = "Live Photo Maker"
__version__ = "1.0.0"
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
VIDEO_COPY_CODECS = {"h264", "hevc"}
REENCODE_CRF = 12  # x264 CRF used only when re-encoding is unavoidable (visually lossless)
APPLE_MAKERNOTE_SIZE = 1568  # what an iPhone writes; older ImageIO ignores MakerNotes under ~1 KB
PHOTO_CLIP_SECONDS = 3.0     # length of the motion clip made from a photo
PHOTO_MAX_VIDEO_SIDE = 1920  # the clip's long side; the still keeps the photo's full size
DEFAULT_ZOOM = 12            # percent


# --------------------------------------------------------------------------- ffmpeg

def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("ffmpeg not found. Run:  pip install imageio-ffmpeg   (or install ffmpeg and add it to PATH)")


def _run(cmd):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")


def probe(path):
    """Read basic stream info by parsing `ffmpeg -i` output (no ffprobe needed)."""
    _, _, err = _run([find_ffmpeg(), "-hide_banner", "-i", path])
    info = {"duration": None, "vcodec": None, "acodec": None, "width": None, "height": None, "fps": None}
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in err.splitlines():
        if "Stream #" not in line:
            continue
        if " Video: " in line and info["vcodec"] is None:
            info["vcodec"] = line.split(" Video: ")[1].split()[0].strip(",")
            m = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
            if m:
                info["width"], info["height"] = int(m.group(1)), int(m.group(2))
            m = re.search(r"([\d.]+)\s*fps", line)
            if m:
                info["fps"] = float(m.group(1))
        elif " Audio: " in line and info["acodec"] is None:
            info["acodec"] = line.split(" Audio: ")[1].split()[0].strip(",")
    if info["vcodec"] is None:
        raise RuntimeError("No video stream found in:\n" + path)
    return info


def extract_frame_png(video, t, max_height=None):
    """Return PNG bytes of the frame at time t (rotation applied, correct colors)."""
    vf = "scale=in_color_matrix=auto:in_range=auto:out_range=full"
    if max_height:
        vf = f"scale=-2:'min({max_height},ih)':in_color_matrix=auto:in_range=auto:out_range=full"
    rc, out, err = _run([find_ffmpeg(), "-v", "error", "-ss", f"{t:.4f}", "-i", video,
                         "-frames:v", "1", "-vf", vf + ",format=rgb24",
                         "-f", "image2pipe", "-vcodec", "png", "-"])
    if rc != 0 or not out:
        raise RuntimeError("Could not extract frame:\n" + err[-800:])
    return out


def extract_filmstrip_png(video, duration, count, thumb_height):
    """Return PNG bytes of `count` evenly spaced thumbnails side by side (for a timeline)."""
    rate = count / max(duration, 0.1)
    vf = (f"fps={rate:.5f},scale=-2:{thumb_height}:in_color_matrix=auto:in_range=auto:out_range=full,"
          f"format=rgb24,tile={count}x1")
    rc, out, err = _run([find_ffmpeg(), "-v", "error", "-i", video, "-an", "-vf", vf,
                         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"])
    if rc != 0 or not out:
        raise RuntimeError("Could not extract thumbnails:\n" + err[-800:])
    return out


# --------------------------------------------------------------------------- JPEG / EXIF

def _ifd(entries, ifd_offset, byte_order=">"):
    """Build a TIFF IFD. entries: list of (tag, type, count, raw_value_bytes).
    Values > 4 bytes are stored right after the IFD; offsets are relative to the
    same base as ifd_offset. Returns bytes of IFD + its data area."""
    entries = sorted(entries, key=lambda e: e[0])
    data_start = ifd_offset + 2 + 12 * len(entries) + 4
    head = struct.pack(byte_order + "H", len(entries))
    data = b""
    for tag, typ, count, raw in entries:
        if len(raw) <= 4:
            head += struct.pack(byte_order + "HHI", tag, typ, count) + raw.ljust(4, b"\0")
        else:
            if (data_start + len(data)) % 2:
                data += b"\0"
            head += struct.pack(byte_order + "HHII", tag, typ, count, data_start + len(data))
            data += raw
    head += struct.pack(byte_order + "I", 0)
    return head + data


def _ascii(s):
    b = s.encode("ascii") + b"\0"
    return b, len(b)


def build_apple_makernote(content_id):
    # "Apple iOS\0" + version 1 + "MM"; IFD follows at offset 14, offsets relative to makernote start
    header = b"Apple iOS\0" + b"\x00\x01" + b"MM"
    cid, cid_len = _ascii(content_id)
    entries = [
        (0x0001, 9, 1, struct.pack(">i", 11)),  # MakerNoteVersion
        (0x0011, 2, cid_len, cid),              # ContentIdentifier  <- pairs with the MOV
    ]
    # Apple's ImageIO silently ignores MakerNotes shorter than ~1 KB, so pad to the
    # size a real iPhone writes (1568 bytes) or iOS never sees the content identifier.
    return (header + _ifd(entries, len(header))).ljust(APPLE_MAKERNOTE_SIZE, b"\0")


def build_exif(content_id, when):
    """Minimal big-endian EXIF block (APP1 payload) with the Apple MakerNote."""
    makernote = build_apple_makernote(content_id)
    dt, dt_len = _ascii(when.strftime("%Y:%m:%d %H:%M:%S"))
    make, make_len = _ascii("Apple")
    soft, soft_len = _ascii(APP_NAME)

    # Lay out: TIFF header (8) | IFD0 | Exif IFD
    ifd0_offset = 8
    ifd0_probe = _ifd([(0x010F, 2, make_len, make), (0x0131, 2, soft_len, soft),
                       (0x8769, 4, 1, b"\0\0\0\0")], ifd0_offset)
    exif_offset = ifd0_offset + len(ifd0_probe)
    exif_offset += exif_offset % 2
    ifd0 = _ifd([(0x010F, 2, make_len, make), (0x0131, 2, soft_len, soft),
                 (0x8769, 4, 1, struct.pack(">I", exif_offset))], ifd0_offset)
    ifd0 = ifd0.ljust(exif_offset - ifd0_offset, b"\0")
    exif_ifd = _ifd([(0x9003, 2, dt_len, dt), (0x9004, 2, dt_len, dt),
                     (0x927C, 7, len(makernote), makernote)], exif_offset)
    return b"Exif\0\0" + b"MM\x00\x2a" + struct.pack(">I", ifd0_offset) + ifd0 + exif_ifd


def insert_exif(jpeg, exif_payload):
    """Replace any APP0/APP1 segments after SOI with our EXIF APP1."""
    if jpeg[:2] != b"\xff\xd8":
        raise RuntimeError("Not a JPEG")
    pos = 2
    while jpeg[pos] == 0xFF and jpeg[pos + 1] in (0xE0, 0xE1):
        pos += 2 + struct.unpack(">H", jpeg[pos + 2:pos + 4])[0]
    if len(exif_payload) + 2 > 0xFFFF:
        raise RuntimeError("EXIF block too large")
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif_payload) + 2) + exif_payload
    return b"\xff\xd8" + app1 + jpeg[pos:]


def make_still_jpeg(video, t, content_id, when, out_path):
    png = extract_frame_png(video, t)
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.open(io.BytesIO(png)).convert("RGB").save(buf, "JPEG", quality=100, subsampling=0, optimize=True)
        jpeg = buf.getvalue()
    except ImportError:  # no Pillow: let ffmpeg encode the JPEG at its highest quality
        tmp = out_path + ".tmp.png"
        with open(tmp, "wb") as f:
            f.write(png)
        rc, jpeg, err = _run([find_ffmpeg(), "-v", "error", "-i", tmp, "-pix_fmt", "yuvj444p",
                              "-q:v", "1", "-qmin", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"])
        os.remove(tmp)
        if rc != 0:
            raise RuntimeError("JPEG encode failed:\n" + err[-800:])
    with open(out_path, "wb") as f:
        f.write(insert_exif(jpeg, build_exif(content_id, when)))


# --------------------------------------------------------------------------- MOV boxes

def _box(typ, payload):
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _full(typ, version, flags, payload):
    return _box(typ, struct.pack(">I", (version << 24) | flags) + payload)


def _read_boxes(data, start, end):
    """Yield (type, box_start, payload_start, box_end) for boxes in data[start:end]."""
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            raise RuntimeError(f"Corrupt box '{typ!r}' at {pos}")
        yield typ, pos, pos + hdr, pos + size
        pos += size


def _top_level_boxes(f, file_size):
    boxes, pos = [], 0
    while pos + 8 <= file_size:
        f.seek(pos)
        size, typ = struct.unpack(">I4s", f.read(8))
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
        elif size == 0:
            size = file_size - pos
        boxes.append((typ, pos, size))
        pos += size
    return boxes


def _still_image_time_track(track_id, video_track_id, movie_ts, still_time, chunk_offset):
    media_ts = 600
    sample_dur = 1  # one tick, like an iPhone
    still_mts = int(round(still_time * movie_ts))
    sample_mts = max(1, int(round(sample_dur / media_ts * movie_ts)))
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)

    tkhd = _full(b"tkhd", 0, 0xF, struct.pack(">IIIII", 0, 0, track_id, 0, still_mts + sample_mts)
                 + b"\0" * 8 + struct.pack(">hhhH", 0, 0, 0, 0) + matrix + struct.pack(">II", 0, 0))
    # 'cdsc' = this metadata track describes the video track (iPhones always write it)
    tref = _box(b"tref", _box(b"cdsc", struct.pack(">I", video_track_id)))

    edits = []
    if still_mts > 0:
        edits.append(struct.pack(">Iii", still_mts, -1, 0x10000))  # empty edit until the still moment
    edits.append(struct.pack(">Iii", sample_mts, 0, 0x10000))
    edts = _box(b"edts", _full(b"elst", 0, 0, struct.pack(">I", len(edits)) + b"".join(edits)))

    mdhd = _full(b"mdhd", 0, 0, struct.pack(">IIIIHH", 0, 0, media_ts, sample_dur, 0x55C4, 0))
    hdlr = _full(b"hdlr", 0, 0, b"mhlr" + b"meta" + b"\0" * 12 + b"\0")
    gmhd = _box(b"gmhd", _full(b"gmin", 0, 0, struct.pack(">HHHHhH", 0x40, 0x8000, 0x8000, 0x8000, 0, 0)))
    dinf = _box(b"dinf", _full(b"dref", 0, 0, struct.pack(">I", 1) + _full(b"alis", 0, 1, b"")))

    key_name = b"com.apple.quicktime.still-image-time"
    key_entry = _box(struct.pack(">I", 1),  # local key id 1
                     _box(b"keyd", b"mdta" + key_name)
                     + _box(b"dtyp", struct.pack(">II", 0, 65)))  # well-known type 65 = signed int8
    mebx = _box(b"mebx", b"\0" * 6 + struct.pack(">H", 1) + _box(b"keys", key_entry))
    stsd = _full(b"stsd", 0, 0, struct.pack(">I", 1) + mebx)
    stts = _full(b"stts", 0, 0, struct.pack(">III", 1, 1, sample_dur))
    stsc = _full(b"stsc", 0, 0, struct.pack(">IIII", 1, 1, 1, 1))
    stsz = _full(b"stsz", 0, 0, struct.pack(">III", 0, 1, 9))
    if chunk_offset < 2 ** 32:
        stco = _full(b"stco", 0, 0, struct.pack(">II", 1, chunk_offset))
    else:
        stco = _full(b"co64", 0, 0, struct.pack(">IQ", 1, chunk_offset))
    stbl = _box(b"stbl", stsd + stts + stsc + stsz + stco)
    minf = _box(b"minf", gmhd + dinf + stbl)
    mdia = _box(b"mdia", mdhd + hdlr + minf)
    return _box(b"trak", tkhd + edts + tref + mdia)


def _take_mdta_meta(moov, start, end):
    """ffmpeg writes the 'mdta' keys under moov/udta/meta (ISO style, with version/flags).
    Apple puts them in moov/meta (QuickTime style, no version/flags). Returns
    (udta without that meta or b"" if empty, QuickTime-style meta box or None)."""
    keep, meta = [], None
    for typ, bs, ps, be in _read_boxes(moov, start, end):
        if typ == b"meta" and meta is None:
            inner_start = ps + 4 if moov[ps + 4:ps + 8] != b"hdlr" else ps
            inner = moov[inner_start:be]
            if b"mdta" in inner[:40]:
                meta = _box(b"meta", inner)
                continue
        keep.append(moov[bs:be])
    return (_box(b"udta", b"".join(keep)) if keep else b""), meta


# One timed-metadata sample: [size=9]['\0\0\0\1' local key id][int8 value = -1]
STILL_SAMPLE = struct.pack(">II", 9, 1) + b"\xff"


def add_still_image_time(mov_in, mov_out, still_time):
    size = os.path.getsize(mov_in)
    with open(mov_in, "rb") as f:
        boxes = _top_level_boxes(f, size)
        types = [b[0] for b in boxes]
        if b"moov" not in types:
            raise RuntimeError("MOV has no moov box")
        mi = types.index(b"moov")
        if any(t == b"mdat" for t in types[mi + 1:]):
            raise RuntimeError("Expected moov after mdat (ffmpeg default); got faststart layout")
        _, moov_pos, moov_size = boxes[mi]
        f.seek(moov_pos)
        moov = f.read(moov_size)

    # Parse moov children
    hdr = 16 if struct.unpack(">I", moov[:4])[0] == 1 else 8
    children = list(_read_boxes(moov, hdr, len(moov)))
    mvhd = next(c for c in children if c[0] == b"mvhd")
    mv = moov[mvhd[2]:mvhd[3]]
    version = mv[0]
    if version == 1:
        movie_ts = struct.unpack(">I", mv[20:24])[0]
        movie_dur = struct.unpack(">Q", mv[24:32])[0]
        next_id_off = mvhd[2] + 4 + 8 + 8 + 4 + 8 + 4 + 2 + 10 + 36 + 24
    else:
        movie_ts = struct.unpack(">I", mv[12:16])[0]
        movie_dur = struct.unpack(">I", mv[16:20])[0]
        next_id_off = mvhd[2] + 4 + 4 + 4 + 4 + 4 + 4 + 2 + 10 + 36 + 24
    next_id = struct.unpack(">I", moov[next_id_off:next_id_off + 4])[0]

    # Highest existing track id (in case next_track_id is stale), and the video track's id
    max_id, video_id = 0, None
    for c in children:
        if c[0] == b"trak":
            tid = None
            for t, _, ps, pe in _read_boxes(moov, c[2], c[3]):
                if t == b"tkhd":
                    v = moov[ps]
                    tid = struct.unpack(">I", moov[ps + (20 if v == 1 else 12):][:4])[0]
                    max_id = max(max_id, tid)
                elif t == b"mdia":
                    for mt, _, mps, _ in _read_boxes(moov, ps, pe):
                        if mt == b"hdlr" and moov[mps + 8:mps + 12] == b"vide" and video_id is None:
                            video_id = tid
    if video_id is None:
        raise RuntimeError("MOV has no video track")
    track_id = max(next_id, max_id + 1)

    # Keep the still moment inside the movie
    movie_seconds = movie_dur / movie_ts if movie_ts else still_time
    still_time = max(0.0, min(still_time, movie_seconds - 0.05))

    # New layout: [everything before moov][extra mdat with our sample][moov + new trak]
    extra_mdat = _box(b"mdat", STILL_SAMPLE)
    sample_offset = moov_pos + 8
    trak = _still_image_time_track(track_id, video_id, movie_ts, still_time, sample_offset)

    parts, mdta_meta = [], None
    for typ, bs, ps, be in children:
        raw = moov[bs:be]
        if typ == b"mvhd":
            raw = bytearray(raw)
            rel = next_id_off - bs
            raw[rel:rel + 4] = struct.pack(">I", track_id + 1)
            raw = bytes(raw)
        elif typ == b"trak":
            raw = bytearray(raw)
            for t, tbs, _, _ in _read_boxes(moov, ps, be):
                if t == b"tkhd":
                    raw[tbs - bs + 11] |= 0xF  # enabled | in movie | in preview | in poster (iPhone sets all four)
            raw = bytes(raw)
        elif typ == b"udta":
            raw, mdta_meta = _take_mdta_meta(moov, ps, be)
        if raw:
            parts.append(raw)
    if mdta_meta is None:
        raise RuntimeError("ffmpeg did not write the content identifier metadata")
    new_moov = _box(b"moov", b"".join(parts) + mdta_meta + trak)

    with open(mov_in, "rb") as src, open(mov_out, "wb") as dst:
        remaining = moov_pos
        while remaining:
            chunk = src.read(min(remaining, 8 << 20))
            dst.write(chunk)
            remaining -= len(chunk)
        dst.write(extra_mdat)
        dst.write(new_moov)
        # anything after moov (e.g. 'free') is dropped - it carries no media data


# --------------------------------------------------------------------------- pipeline

def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def output_paths(src, out_dir=None):
    """name_live.JPG / name_live.MOV next to src (or in out_dir), never overwriting."""
    out_dir = out_dir or os.path.dirname(os.path.abspath(src))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0] + "_live"
    name, n = base, 1
    while any(os.path.exists(os.path.join(out_dir, name + ext)) for ext in (".JPG", ".MOV")):
        n += 1
        name = f"{base}{n}"
    return os.path.join(out_dir, name + ".JPG"), os.path.join(out_dir, name + ".MOV")


def _mov_output_args(content_id, now, out_mov):
    return ["-f", "mov", "-movflags", "use_metadata_tags",
            "-metadata", f"com.apple.quicktime.content.identifier={content_id}",
            "-metadata", f"com.apple.quicktime.creationdate={now.strftime('%Y-%m-%dT%H:%M:%S%z')}",
            "-metadata", f"creation_time={now.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000000Z')}",
            out_mov]


def plan_video(info, start=None, duration=None):
    """What convert() will do with this input: (copies_video_losslessly, reason_if_not)."""
    total = info["duration"] or 0.0
    trimming = (start not in (None, 0, 0.0)) or (duration is not None and duration < total - 0.01)
    if info["vcodec"] not in VIDEO_COPY_CODECS:
        return False, f"{info['vcodec']} is not playable on iPhone"
    if trimming:
        return False, "trimming needs frame-accurate cuts"
    return True, None


def convert(src, out_dir=None, still_time=None, start=None, duration=None, log=print):
    """Video -> Live Photo. still_time is relative to the (trimmed) clip; default: middle.
    Returns (jpg_path, mov_path)."""
    ff = find_ffmpeg()
    info = probe(src)
    total = info["duration"] or 0.0
    clip_len = min(duration, total - (start or 0)) if duration else total - (start or 0)
    if still_time is None:
        still_time = clip_len / 2
    still_time = max(0.0, min(still_time, max(0.0, clip_len - 0.05)))
    out_jpg, out_mov = output_paths(src, out_dir)

    content_id = str(uuid.uuid4()).upper()
    now = datetime.datetime.now().astimezone()
    log(f"Input: {info['vcodec']} {info['width']}x{info['height']} @ {info['fps']} fps, "
        f"audio: {info['acodec'] or 'none'}, {total:.2f}s")

    copy_video, why = plan_video(info, start, duration)
    cmd = [ff, "-y", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-i", src]
    if duration:
        cmd += ["-t", f"{duration:.4f}"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_chapters", "-1"]
    if copy_video:
        log("Video: lossless stream copy (no re-encode)")
        cmd += ["-c:v", "copy"]
        if info["vcodec"] == "hevc":
            cmd += ["-tag:v", "hvc1"]
    else:
        log(f"Video: re-encoding with H.264 CRF {REENCODE_CRF} (visually lossless) - {why}")
        cmd += ["-c:v", "libx264", "-crf", str(REENCODE_CRF), "-preset", "slow",
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-tag:v", "avc1"]
    if info["acodec"] == "aac":
        cmd += ["-c:a", "copy"]
    elif info["acodec"]:
        log(f"Audio: {info['acodec']} -> AAC 256k")
        cmd += ["-c:a", "aac", "-b:a", "256k"]

    tmp_dir = tempfile.mkdtemp(prefix="livephoto_")
    try:
        tmp_mov = os.path.join(tmp_dir, "stage1.mov")
        rc, _, err = _run(cmd + _mov_output_args(content_id, now, tmp_mov))
        if rc != 0:
            raise RuntimeError("ffmpeg failed:\n" + err[-1500:])

        log(f"Adding still-image-time marker at {still_time:.3f}s")
        add_still_image_time(tmp_mov, out_mov, still_time)

        log("Extracting key photo (full resolution, JPEG q100 4:4:4)")
        make_still_jpeg(out_mov, still_time, content_id, now, out_jpg)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    log(f"Content identifier: {content_id}")
    log(f"Done:\n  {out_jpg}\n  {out_mov}")
    return out_jpg, out_mov


def load_photo(path):
    """Open a photo upright. Returns (PIL image in RGB, original JPEG bytes or None).
    The original bytes are returned only when they can be used as the still as-is."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise RuntimeError("Photos need Pillow. Run:  pip install pillow")
    img = Image.open(path)
    orientation = img.getexif().get(0x0112, 1)
    original = None
    if img.format == "JPEG" and orientation in (0, 1) and img.mode in ("RGB", "L"):
        with open(path, "rb") as f:
            original = f.read()
    return ImageOps.exif_transpose(img).convert("RGB"), original


def convert_photo(src, out_dir=None, zoom=DEFAULT_ZOOM, log=print):
    """Photo -> Live Photo: a 3 s clip that eases out of a zoom to the full photo (the
    still, in the middle) and back in. zoom is in percent. Returns (jpg_path, mov_path)."""
    ff = find_ffmpeg()
    img, original = load_photo(src)
    w, h = img.size
    scale = min(1.0, PHOTO_MAX_VIDEO_SIDE / max(w, h))
    vw, vh = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
    frames = int(round(PHOTO_CLIP_SECONDS * 30))
    out_jpg, out_mov = output_paths(src, out_dir)
    content_id = str(uuid.uuid4()).upper()
    now = datetime.datetime.now().astimezone()
    log(f"Input: photo {w}x{h}; motion clip {vw}x{vh}, {PHOTO_CLIP_SECONDS:.0f}s, zoom {zoom:g}%")

    # zoompan crops on whole pixels, so work on a 4x upscale to keep the motion smooth
    z = f"1+{zoom / 100:.4f}*(1+cos(2*PI*on/{frames}))/2"
    vf = (f"scale={vw * 4}:{vh * 4}:flags=lanczos,"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={vw}x{vh}:fps=30,"
          "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p")
    tmp_dir = tempfile.mkdtemp(prefix="livephoto_")
    try:
        png = os.path.join(tmp_dir, "photo.png")
        img.save(png)
        tmp_mov = os.path.join(tmp_dir, "stage1.mov")
        cmd = [ff, "-y", "-v", "error", "-i", png,
               "-f", "lavfi", "-t", f"{PHOTO_CLIP_SECONDS}", "-i", "anullsrc=r=44100:cl=stereo",
               "-vf", vf, "-frames:v", str(frames),
               "-c:v", "libx264", "-crf", str(REENCODE_CRF), "-preset", "slow", "-profile:v", "high",
               "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709", "-tag:v", "avc1",
               "-c:a", "aac", "-b:a", "128k", "-shortest"]
        log("Rendering motion clip (H.264, visually lossless)")
        rc, _, err = _run(cmd + _mov_output_args(content_id, now, tmp_mov))
        if rc != 0:
            raise RuntimeError("ffmpeg failed:\n" + err[-1500:])

        log(f"Adding still-image-time marker at {PHOTO_CLIP_SECONDS / 2:.3f}s")
        add_still_image_time(tmp_mov, out_mov, PHOTO_CLIP_SECONDS / 2)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if original:
        log("Key photo: your original JPEG, untouched")
        jpeg = original
    else:
        log("Key photo: full resolution, JPEG q100 4:4:4")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=100, subsampling=0, optimize=True)
        jpeg = buf.getvalue()
    with open(out_jpg, "wb") as f:
        f.write(insert_exif(jpeg, build_exif(content_id, now)))

    log(f"Content identifier: {content_id}")
    log(f"Done:\n  {out_jpg}\n  {out_mov}")
    return out_jpg, out_mov


# --------------------------------------------------------------------------- macOS Photos

_PHOTOS_IMPORT = """on run argv
set f1 to (POSIX file (item 1 of argv)) as alias
set f2 to (POSIX file (item 2 of argv)) as alias
tell application "Photos"
    set imported to import {f1, f2} skip check duplicates true
    return count of imported
end tell
end run"""


def add_to_photos(jpg, mov):
    """macOS: import both files into the Photos app at once, which pairs them into one
    Live Photo (and syncs it to the iPhone when iCloud Photos is on)."""
    if sys.platform != "darwin":
        raise RuntimeError("Adding to Photos only works on a Mac")
    rc, out, err = _run(["osascript", "-e", _PHOTOS_IMPORT, os.path.abspath(jpg), os.path.abspath(mov)])
    if rc != 0:
        raise RuntimeError("Photos import failed:\n" + err.strip())
    if out.decode().strip() != "1":
        raise RuntimeError("Photos imported the files separately instead of as one Live Photo")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Turn a video or photo into an iPhone Live Photo (JPEG + MOV).")
    ap.add_argument("input", nargs="?", help="video or photo")
    ap.add_argument("--cli", action="store_true", help="convert without opening the app")
    ap.add_argument("--still", type=float, help="video: key photo time in seconds (relative to the clip; default: middle)")
    ap.add_argument("--start", type=float, help="video: trim start in seconds")
    ap.add_argument("--duration", type=float, help="video: trim length in seconds")
    ap.add_argument("--zoom", type=float, default=DEFAULT_ZOOM, help=f"photo: zoom amount in percent (default {DEFAULT_ZOOM})")
    ap.add_argument("-o", "--out", help="output folder (default: next to the input)")
    ap.add_argument("--add-to-photos", action="store_true", help="macOS: import the result into the Photos app")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    a = ap.parse_args()
    if not a.cli:
        from livephoto_gui import run_gui
        run_gui(a.input)
        return
    if not a.input:
        ap.error("an input file is required with --cli")
    try:
        if is_image(a.input):
            jpg, mov = convert_photo(a.input, a.out, a.zoom)
        else:
            jpg, mov = convert(a.input, a.out, a.still, a.start, a.duration)
        if a.add_to_photos:
            add_to_photos(jpg, mov)
            print("Added to Photos as a Live Photo")
    except RuntimeError as ex:
        sys.exit(f"Error: {ex}")


if __name__ == "__main__":
    main()

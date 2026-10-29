# Live Photo Maker

Convert videos and photos into iPhone Live Photos without re-encoding.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)
[![Web demo](https://img.shields.io/badge/demo-online-brightgreen.svg)](https://efella.github.io/LivePhoto/web/)

Live Photo Maker produces the JPEG + MOV pair that iOS and macOS recognise as a single
Live Photo. The video stream is remuxed as is, so it is never decoded or re-compressed.
The key photo is the exact frame you choose.

The project has two parts:

- **Python:** a desktop app, a command-line tool and an importable module.
- **JavaScript:** a library with no dependencies that does the same conversion entirely
  in the browser. A [live demo](https://efella.github.io/LivePhoto/web/) is available.

## Features

- **Lossless video.** H.264 and HEVC streams are copied bit-for-bit.
- **Frame-accurate key photo.** Exported at full resolution as a 4:4:4, quality-100 JPEG.
- **Optional trimming.** Trimmed clips are re-encoded with x264 at CRF 12.
- **Photo input.** A photo becomes a 3-second motion clip. The original JPEG is kept,
  unchanged, as the key photo.
- **Desktop app** (Tkinter) with a filmstrip timeline, trim handles and drag and drop.
- **Direct import** into Photos on macOS.
- **Browser and Node.js library.** Files are processed on the client and never uploaded.

## Installation

**Requirements**

- Python 3.8 or later, with Tk 8.6 or later.
- ffmpeg: bundled through `imageio-ffmpeg`, or picked up from `PATH` if installed.
- Pillow.
- Optional: `tkinterdnd2`, which enables drag and drop.

**Install**

```bash
git clone https://github.com/efella/LivePhoto.git
cd LivePhoto
pip install -r requirements.txt
pip install tkinterdnd2   # optional
```

**Launchers.** `Live Photo Maker.bat` (Windows) and `Live Photo Maker.command` (macOS)
start the app by double-click. On first run they create a local `.venv` and install the
requirements into it.

**macOS.** Apple's bundled `/usr/bin/python3` ships Tk 8.5, which cannot open windows on
current macOS. Use Python from [python.org](https://www.python.org/downloads/), or
Homebrew (`brew install python python-tk`).

## Usage

### Desktop app

```bash
python livephoto.py [video-or-photo]
```

1. Open a video or photo.
2. Choose the key photo on the filmstrip: click, drag, or use the arrow keys to step one
   frame.
3. Optionally, drag the trim handles to shorten the clip.

The status label shows whether the video will be copied losslessly or re-encoded.

Output is written next to the source file as `<name>_live.JPG` and `<name>_live.MOV`.
Existing files are never overwritten.

### Command line

```bash
python livephoto.py video.mp4 --cli                       # key photo at the midpoint
python livephoto.py video.mp4 --cli --still 1.2 -o out/   # key photo at 1.2 s
python livephoto.py video.mp4 --cli --start 0.5 --duration 3
python livephoto.py photo.jpg --cli --zoom 15             # photo input, 15 % zoom
python livephoto.py video.mp4 --cli --add-to-photos       # macOS: import into Photos
```

Run `python livephoto.py --help` for the full list of options.

### Python API

```python
import livephoto

jpg, mov = livephoto.convert("video.mp4", out_dir="out", still_time=1.5)
jpg, mov = livephoto.convert_photo("photo.jpg", out_dir="out", zoom=12)
livephoto.add_to_photos(jpg, mov)  # macOS only
```

### Importing

The two files must be imported **together** to be paired. Importing both into Photos on
macOS produces a Live Photo, and it syncs to iOS through iCloud Photos.

On iOS the motion becomes available once the paired video has downloaded to the device.
Until then the Live Photo appears still.

## Web library

[`web/livephoto.js`](web/livephoto.js) is a single UMD script with no dependencies. It
works with a `<script>` tag, with CommonJS, and in Node.js. It edits the MP4/MOV
container directly, so it needs no ffmpeg or server.

[`web/index.html`](web/index.html) is a complete reference page, deployed at
[efella.github.io/LivePhoto/web](https://efella.github.io/LivePhoto/web/).

```html
<input type="file" id="pick" accept="video/mp4,video/quicktime">
<script src="livephoto.js"></script>
<script>
  document.getElementById("pick").onchange = async (e) => {
    const live = await LivePhotoMaker.makeLivePhoto(e.target.files[0], { stillTime: 1.5 });
    // live.photo (image/jpeg) and live.video (video/quicktime) are Blobs;
    // live.names holds suggested file names.
  };
</script>
```

| Function | Description |
|---|---|
| `makeLivePhoto(file, options)` | Resolves to `{ photo, video, contentId, stillTime, names }`. |
| `buildLiveMov(bytes, { stillTime, contentId })` | Builds the MOV from a `Uint8Array`. Returns `{ parts }` for `new Blob(parts)`. Works in Node.js. |
| `addLivePhotoExif(jpegBytes, contentId)` | Adds the Live Photo metadata to a JPEG. Works in Node.js. |
| `readVideoInfo(bytes)` | Returns `{ duration, codec, copyable, … }`. |

| Option | Default | Description |
|---|---|---|
| `stillTime` | midpoint | Key photo time, in seconds. |
| `photo` | – | A JPEG `Blob` to use as the key photo instead of a video frame. |
| `jpegQuality` | `1` | Quality used when encoding the captured frame. |

Limitations of the web version:

- Input must be MP4 or MOV with H.264 (`avc1`) or HEVC (`hvc1`). Trimming and
  transcoding require the Python version.
- The key photo is captured through a `<canvas>`. It is slightly lower in fidelity than
  the Python version: about 39 dB PSNR, against about 57 dB.
- Capturing the frame requires the browser to decode the video, and HEVC support varies
  between browsers.

## How it works

A Live Photo is a still image and a QuickTime movie linked by a shared UUID, the
*content identifier*:

```
 <name>_live.JPG                        <name>_live.MOV
┌───────────────────────────┐         ┌─────────────────────────────────────────────┐
│ EXIF                      │         │ video and audio tracks (remuxed, unchanged)  │
│  └ Apple MakerNote        │         │ moov/meta                                    │
│     └ tag 0x11 = <UUID>  ◄┼─────────┼─► com.apple.quicktime.content.identifier    │
│ JPEG image data           │         │ timed metadata track                         │
└───────────────────────────┘         │  └ still-image-time at the key photo time    │
                                      └─────────────────────────────────────────────┘
```

**JPEG.** A big-endian EXIF block containing an Apple MakerNote (`Apple iOS` header).
- Tag `0x11` holds the content identifier.
- The MakerNote is padded to 1568 bytes, the size written by iPhones. Some ImageIO
  versions ignore MakerNotes smaller than about 1 KB.

**MOV.** ffmpeg remuxes the source with `-c:v copy` and writes the content identifier
as an `mdta` metadata key. `add_still_image_time()` then edits the box structure:

- It moves the metadata from ISO-style `moov/udta/meta` to QuickTime-style `moov/meta`.
- It appends a timed metadata track:
  - handler `meta`, sample entry `mebx`, key `com.apple.quicktime.still-image-time`
    (int8);
  - a single sample with the value `-1`, positioned at the key photo time by an empty
    edit;
  - a `tref/cdsc` reference to the video track.
- It sets the enabled, in-movie, in-preview and in-poster flags on every track, as
  iPhone files do.

**Photo input.** A cosine zoom curve is rendered to H.264 with the unzoomed frame
exactly at the midpoint, which is the still-image-time.

**Validation.** Output is checked with Apple's ImageIO, AVFoundation and
`PHLivePhoto`, and compared against Live Photos captured on an iPhone.

## Known limitations

- Uploading through icloud.com, or through the iCloud for Windows upload folder, imports
  the two files as separate items. No Windows-only import route has been verified yet.
- When input needs transcoding (trimming, or codecs other than H.264/HEVC), output is
  visually lossless rather than bit-exact.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Live badge appears on iOS but the photo does not move | The paired video has not downloaded yet. Wait for iCloud to finish syncing. |
| Photo and video appear as separate items | The files were imported separately. Import both at once. |
| macOS: the window does not open, or the launcher reports Tk 8.5 | Install Python from python.org, then delete `.venv` so the launcher rebuilds it. |
| Windows: "Python 3 was not found" | Install Python from python.org with **Add python.exe to PATH** enabled. |
| Drag and drop is unavailable | `pip install tkinterdnd2` |

## Project structure

| Path | Description |
|---|---|
| `livephoto.py` | Conversion engine, CLI and Python API |
| `livephoto_gui.py` | Desktop app (Tkinter) |
| `Live Photo Maker.bat`, `Live Photo Maker.command` | Launchers for Windows and macOS |
| `web/livephoto.js` | Browser and Node.js library |
| `web/index.html` | Reference web page |

## Contributing

Issues and pull requests are welcome. When reporting a conversion problem, include the
log from **Show details** in the desktop app, or the CLI output, along with the source
file's codec and container.

## Acknowledgements

- [ExifTool](https://exiftool.org/TagNames/Apple.html): documentation of Apple MakerNote
  and QuickTime tags.
- [MotionPhotoConverter](https://github.com/lutao043/MotionPhotoConverter): notes on
  MakerNote size requirements.

## License

Released under the [MIT License](LICENSE).

Live Photos and iPhone are trademarks of Apple Inc. This project is not affiliated with
Apple.

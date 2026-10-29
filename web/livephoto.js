/*!
 * Live Photo Maker for the web - turns a video into an iPhone Live Photo (JPEG + MOV)
 * right in the browser. Nothing is uploaded. MIT License.
 *
 *   <script src="livephoto.js"></script>
 *   const { photo, video } = await LivePhotoMaker.makeLivePhoto(file, { stillTime: 1.5 });
 *
 * The video stream is copied bit-for-bit (H.264 or HEVC in an MP4/MOV container). The still
 * is the frame at stillTime (or a JPEG you pass in). Same file format as livephoto.py.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.LivePhotoMaker = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var MAKERNOTE_SIZE = 1568; // what an iPhone writes; older ImageIO ignores MakerNotes under ~1 KB
  var COPYABLE = { avc1: "H.264", hvc1: "HEVC" };

  // ------------------------------------------------------------------ bytes

  function ascii(s) {
    var b = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) b[i] = s.charCodeAt(i) & 0xff;
    return b;
  }
  function utf8(s) { return new TextEncoder().encode(s); }
  function u16(n) { var b = new Uint8Array(2); new DataView(b.buffer).setUint16(0, n); return b; }
  function u32(n) { var b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n >>> 0); return b; }
  function i32(n) { var b = new Uint8Array(4); new DataView(b.buffer).setInt32(0, n); return b; }
  function zeros(n) { return new Uint8Array(n); }
  function concat(parts) {
    var len = 0, i, off = 0;
    for (i = 0; i < parts.length; i++) len += parts[i].length;
    var out = new Uint8Array(len);
    for (i = 0; i < parts.length; i++) { out.set(parts[i], off); off += parts[i].length; }
    return out;
  }
  function type4(t) { return typeof t === "string" ? ascii(t) : t; }
  function indexOf(hay, s) {
    var needle = ascii(s), first = needle[0];
    outer: for (var i = hay.indexOf(first); i >= 0 && i <= hay.length - needle.length; i = hay.indexOf(first, i + 1)) {
      for (var j = 1; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
      return i;
    }
    return -1;
  }

  // ------------------------------------------------------------------ MOV boxes

  function box(type) {
    var body = concat(Array.prototype.slice.call(arguments, 1));
    return concat([u32(8 + body.length), type4(type), body]);
  }
  function fullBox(type, version, flags) {
    var rest = Array.prototype.slice.call(arguments, 3);
    return box.apply(null, [type, u32((version << 24) | flags)].concat(rest));
  }

  /** [{type, start, payload, end}] for the boxes in data[start:end]. */
  function readBoxes(data, start, end) {
    var dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    var out = [], pos = start;
    while (pos + 8 <= end) {
      var size = dv.getUint32(pos), hdr = 8;
      var type = String.fromCharCode(data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7]);
      if (size === 1) { size = dv.getUint32(pos + 8) * 4294967296 + dv.getUint32(pos + 12); hdr = 16; }
      else if (size === 0) size = end - pos;
      if (size < hdr || pos + size > end) throw new Error("Corrupt video file (box '" + type + "' at " + pos + ")");
      out.push({ type: type, start: pos, payload: pos + hdr, end: pos + size });
      pos += size;
    }
    return out;
  }
  function child(data, parent, type) {
    var list = readBoxes(data, parent.payload, parent.end);
    for (var i = 0; i < list.length; i++) if (list[i].type === type) return list[i];
    return null;
  }
  function path(data, parent, types) {
    for (var i = 0; i < types.length && parent; i++) parent = child(data, parent, types[i]);
    return parent;
  }

  function stillImageTimeTrack(trackId, videoTrackId, movieTs, stillTime, chunkOffset) {
    var mediaTs = 600, sampleDur = 1; // one tick, like an iPhone
    var stillMts = Math.round(stillTime * movieTs);
    var sampleMts = Math.max(1, Math.round(sampleDur / mediaTs * movieTs));
    var matrix = concat([u32(0x10000), u32(0), u32(0), u32(0), u32(0x10000), u32(0), u32(0), u32(0), u32(0x40000000)]);

    var tkhd = fullBox("tkhd", 0, 0xf, u32(0), u32(0), u32(trackId), u32(0), u32(stillMts + sampleMts),
      zeros(8), zeros(8), matrix, u32(0), u32(0));
    // 'cdsc' = this metadata track describes the video track (iPhones always write it)
    var tref = box("tref", box("cdsc", u32(videoTrackId)));
    var edits = [];
    if (stillMts > 0) edits.push(concat([u32(stillMts), i32(-1), u32(0x10000)])); // empty edit until the still
    edits.push(concat([u32(sampleMts), i32(0), u32(0x10000)]));
    var edts = box("edts", fullBox("elst", 0, 0, u32(edits.length), concat(edits)));

    var mdhd = fullBox("mdhd", 0, 0, u32(0), u32(0), u32(mediaTs), u32(sampleDur), u16(0x55c4), u16(0));
    var hdlr = fullBox("hdlr", 0, 0, ascii("mhlrmeta"), zeros(12), zeros(1));
    var gmhd = box("gmhd", fullBox("gmin", 0, 0, u16(0x40), u16(0x8000), u16(0x8000), u16(0x8000), u16(0), u16(0)));
    var dinf = box("dinf", fullBox("dref", 0, 0, u32(1), fullBox("alis", 0, 1)));

    var keyEntry = box(u32(1), // local key id 1
      box("keyd", ascii("mdta"), ascii("com.apple.quicktime.still-image-time")),
      box("dtyp", u32(0), u32(65))); // well-known type 65 = signed int8
    var mebx = box("mebx", zeros(6), u16(1), box("keys", keyEntry));
    var stbl = box("stbl",
      fullBox("stsd", 0, 0, u32(1), mebx),
      fullBox("stts", 0, 0, u32(1), u32(1), u32(sampleDur)),
      fullBox("stsc", 0, 0, u32(1), u32(1), u32(1), u32(1)),
      fullBox("stsz", 0, 0, u32(0), u32(1), u32(9)),
      fullBox("stco", 0, 0, u32(1), u32(chunkOffset)));
    var mdia = box("mdia", mdhd, hdlr, box("minf", gmhd, dinf, stbl));
    return box("trak", tkhd, edts, tref, mdia);
  }

  /** QuickTime-style moov/meta (no version/flags) with 'mdta' string keys. */
  function mdtaMeta(items) {
    var keys = [], vals = [];
    items.forEach(function (kv, i) {
      var name = ascii(kv[0]);
      keys.push(concat([u32(8 + name.length), ascii("mdta"), name]));
      vals.push(box(u32(i + 1), box("data", u32(1), u32(0), utf8(kv[1])))); // type 1 = UTF-8
    });
    return box("meta",
      fullBox("hdlr", 0, 0, u32(0), ascii("mdta"), zeros(12), zeros(1)),
      fullBox("keys", 0, 0, u32(items.length), concat(keys)),
      box("ilst", concat(vals)));
  }

  function isoDate(d) {
    function p(n) { return (n < 10 ? "0" : "") + n; }
    var off = -d.getTimezoneOffset(), sign = off >= 0 ? "+" : "-";
    off = Math.abs(off);
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" +
      p(d.getMinutes()) + ":" + p(d.getSeconds()) + sign + p(Math.floor(off / 60)) + p(off % 60);
  }

  /** Basic facts about an MP4/MOV: {duration, timescale, codec, copyable, videoTrackId}. */
  function readVideoInfo(bytes) {
    var data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    var top = readBoxes(data, 0, data.length);
    var moov = top.filter(function (b) { return b.type === "moov"; })[0];
    if (!moov) throw new Error("Not an MP4/MOV video (no 'moov' box)");
    var dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    var mvhd = child(data, moov, "mvhd");
    var v = data[mvhd.payload];
    var ts = dv.getUint32(mvhd.payload + (v === 1 ? 20 : 12));
    var dur = v === 1 ? dv.getUint32(mvhd.payload + 24) * 4294967296 + dv.getUint32(mvhd.payload + 28)
      : dv.getUint32(mvhd.payload + 16);
    var info = { duration: ts ? dur / ts : 0, timescale: ts, codec: null, copyable: false, videoTrackId: null };
    readBoxes(data, moov.payload, moov.end).forEach(function (t) {
      if (t.type !== "trak" || info.videoTrackId !== null) return;
      var hdlr = path(data, t, ["mdia", "hdlr"]);
      if (!hdlr || String.fromCharCode.apply(null, data.subarray(hdlr.payload + 8, hdlr.payload + 12)) !== "vide") return;
      var tkhd = child(data, t, "tkhd");
      info.videoTrackId = dv.getUint32(tkhd.payload + (data[tkhd.payload] === 1 ? 20 : 12));
      var stsd = path(data, t, ["mdia", "minf", "stbl", "stsd"]);
      info.codec = String.fromCharCode.apply(null, data.subarray(stsd.payload + 12, stsd.payload + 16));
      info.copyable = !!COPYABLE[info.codec];
    });
    if (info.videoTrackId === null) throw new Error("This file has no video track");
    return info;
  }

  /**
   * Turn an MP4/MOV into the MOV half of a Live Photo without re-encoding.
   * Returns {parts: Uint8Array[], stillTime, duration}; new Blob(parts) is the .MOV file.
   */
  function buildLiveMov(bytes, opts) {
    opts = opts || {};
    var data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    var info = readVideoInfo(data);
    if (!info.copyable) {
      throw new Error("The video is " + (info.codec || "an unknown codec") + "; iPhones need H.264 (avc1) or HEVC (hvc1). " +
        "Use the desktop app, which re-encodes it.");
    }
    var dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    var top = readBoxes(data, 0, data.length);
    var moov = top.filter(function (b) { return b.type === "moov"; })[0];
    var stillTime = opts.stillTime == null ? info.duration / 2 : opts.stillTime;
    stillTime = Math.max(0, Math.min(stillTime, Math.max(0, info.duration - 0.05)));

    // New layout: ftyp wide [original media boxes] mdat(still sample) moov
    var head = [box("ftyp", ascii("qt  "), u32(0x20050300), ascii("qt  ")), box("wide")];
    var pos = head[0].length + head[1].length;
    var kept = [];
    top.forEach(function (b) {
      if (b.type === "ftyp" || b.type === "moov") return;
      kept.push({ start: b.start, end: b.end, delta: pos - b.start });
      pos += b.end - b.start;
    });
    var STILL_SAMPLE = concat([u32(9), u32(1), new Uint8Array([0xff])]); // [size][local key 1][int8 -1]
    var extraMdat = box("mdat", STILL_SAMPLE);
    var sampleOffset = pos + 8;

    function moved(offset) {
      for (var i = 0; i < kept.length; i++) if (offset >= kept[i].start && offset < kept[i].end) return offset + kept[i].delta;
      throw new Error("Video sample data is outside the media boxes");
    }

    var mvhd = child(data, moov, "mvhd");
    var v = data[mvhd.payload];
    var nextIdOff = mvhd.payload + (v === 1 ? 108 : 96);
    var maxId = 0, parts = [];
    readBoxes(data, moov.payload, moov.end).forEach(function (c) {
      if (c.type === "trak") {
        var t = data.slice(c.start, c.end), tdv = new DataView(t.buffer);
        var tkhd = child(data, c, "tkhd");
        maxId = Math.max(maxId, dv.getUint32(tkhd.payload + (data[tkhd.payload] === 1 ? 20 : 12)));
        if (indexOf(t, "com.apple.quicktime.still-image-time") >= 0) return; // already a Live Photo: replace its marker
        t[tkhd.start - c.start + 11] |= 0xf; // enabled | in movie | in preview | in poster
        var stbl = path(data, c, ["mdia", "minf", "stbl"]);
        var co = stbl && (child(data, stbl, "stco") || child(data, stbl, "co64"));
        if (co) {
          var n = dv.getUint32(co.payload + 4), base = co.payload + 8 - c.start, i;
          for (i = 0; i < n; i++) {
            if (co.type === "stco") {
              var o = moved(tdv.getUint32(base + 4 * i));
              if (o >= 4294967296) throw new Error("Video too large");
              tdv.setUint32(base + 4 * i, o);
            } else {
              var hi = tdv.getUint32(base + 8 * i), lo = tdv.getUint32(base + 8 * i + 4);
              var o64 = moved(hi * 4294967296 + lo);
              tdv.setUint32(base + 8 * i, Math.floor(o64 / 4294967296));
              tdv.setUint32(base + 8 * i + 4, o64 % 4294967296);
            }
          }
        }
        parts.push(t);
      } else if (c.type !== "udta" && c.type !== "meta" && c.type !== "mvhd") {
        parts.push(data.subarray(c.start, c.end)); // drop old metadata; everything else stays
      }
    });
    var trackId = Math.max(dv.getUint32(nextIdOff), maxId + 1);
    var newMvhd = data.slice(mvhd.start, mvhd.end);
    new DataView(newMvhd.buffer).setUint32(nextIdOff - mvhd.start, trackId + 1);

    var contentId = opts.contentId || uuid();
    var meta = mdtaMeta([
      ["com.apple.quicktime.content.identifier", contentId],
      ["com.apple.quicktime.creationdate", isoDate(opts.creationDate || new Date())]
    ]);
    var trak = stillImageTimeTrack(trackId, info.videoTrackId, info.timescale, stillTime, sampleOffset);
    var newMoov = box.apply(null, ["moov", newMvhd].concat(parts, [meta, trak]));

    var out = head.slice();
    kept.forEach(function (k) { out.push(data.subarray(k.start, k.end)); });
    out.push(extraMdat, newMoov);
    return { parts: out, stillTime: stillTime, duration: info.duration, contentId: contentId };
  }

  // ------------------------------------------------------------------ JPEG / EXIF

  function ifd(entries, ifdOffset) {
    entries = entries.slice().sort(function (a, b) { return a[0] - b[0]; });
    var dataStart = ifdOffset + 2 + 12 * entries.length + 4;
    var head = [u16(entries.length)], data = [], dataLen = 0;
    entries.forEach(function (e) {
      var tag = e[0], typ = e[1], count = e[2], raw = e[3];
      if (raw.length <= 4) {
        var v = zeros(4); v.set(raw);
        head.push(u16(tag), u16(typ), u32(count), v);
      } else {
        if ((dataStart + dataLen) % 2) { data.push(zeros(1)); dataLen += 1; }
        head.push(u16(tag), u16(typ), u32(count), u32(dataStart + dataLen));
        data.push(raw); dataLen += raw.length;
      }
    });
    head.push(u32(0));
    return concat(head.concat(data));
  }
  function asciiz(s) { return concat([ascii(s), zeros(1)]); }

  function appleMakerNote(contentId) {
    var header = concat([ascii("Apple iOS\0"), new Uint8Array([0, 1]), ascii("MM")]);
    var cid = asciiz(contentId);
    var note = concat([header, ifd([
      [0x0001, 9, 1, i32(11)],        // MakerNoteVersion
      [0x0011, 2, cid.length, cid]    // ContentIdentifier <- pairs with the MOV
    ], header.length)]);
    var out = zeros(Math.max(MAKERNOTE_SIZE, note.length));
    out.set(note);
    return out;
  }

  function exifPayload(contentId, when) {
    function p(n) { return (n < 10 ? "0" : "") + n; }
    var dt = asciiz(when.getFullYear() + ":" + p(when.getMonth() + 1) + ":" + p(when.getDate()) + " " +
      p(when.getHours()) + ":" + p(when.getMinutes()) + ":" + p(when.getSeconds()));
    var make = asciiz("Apple"), soft = asciiz("Live Photo Maker");
    var note = appleMakerNote(contentId);
    function ifd0(exifOffset) {
      return ifd([[0x010f, 2, make.length, make], [0x0131, 2, soft.length, soft], [0x8769, 4, 1, u32(exifOffset)]], 8);
    }
    var exifOffset = 8 + ifd0(0).length;
    exifOffset += exifOffset % 2;
    var i0 = ifd0(exifOffset), padded = zeros(exifOffset - 8);
    padded.set(i0);
    var exif = ifd([[0x9003, 2, dt.length, dt], [0x9004, 2, dt.length, dt], [0x927c, 7, note.length, note]], exifOffset);
    return concat([ascii("Exif\0\0"), ascii("MM"), u16(0x2a), u32(8), padded, exif]);
  }

  /** Add the Live Photo EXIF (Apple MakerNote with the content identifier) to a JPEG. */
  function addLivePhotoExif(jpegBytes, contentId, when) {
    var jpeg = jpegBytes instanceof Uint8Array ? jpegBytes : new Uint8Array(jpegBytes);
    if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8) throw new Error("The key photo is not a JPEG");
    var pos = 2;
    while (jpeg[pos] === 0xff && (jpeg[pos + 1] === 0xe0 || jpeg[pos + 1] === 0xe1)) {
      pos += 2 + ((jpeg[pos + 2] << 8) | jpeg[pos + 3]); // drop JFIF / old EXIF
    }
    var payload = exifPayload(contentId, when || new Date());
    if (payload.length + 2 > 0xffff) throw new Error("EXIF block too large");
    return concat([new Uint8Array([0xff, 0xd8, 0xff, 0xe1]), u16(payload.length + 2), payload, jpeg.subarray(pos)]);
  }

  // ------------------------------------------------------------------ browser helpers

  function uuid() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID().toUpperCase();
    var b = new Uint8Array(16);
    crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return (x + 0x100).toString(16).slice(1); }).join("");
    return (h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20)).toUpperCase();
  }

  function once(el, ok, fail) {
    return new Promise(function (resolve, reject) {
      function done(e) { el.removeEventListener(ok, done); el.removeEventListener(fail, err); resolve(e); }
      function err() { el.removeEventListener(ok, done); el.removeEventListener(fail, err); reject(new Error("decode")); }
      el.addEventListener(ok, done);
      el.addEventListener(fail, err);
    });
  }

  /** JPEG Blob of the video frame at `time` seconds, at the video's full resolution. */
  function grabFrame(file, time, quality) {
    var url = URL.createObjectURL(file);
    var v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.preload = "auto"; v.src = url;
    return once(v, "loadeddata", "error").then(function () {
      v.currentTime = Math.min(time, Math.max(0, v.duration - 0.001));
      return once(v, "seeked", "error");
    }).then(function () {
      var c = document.createElement("canvas");
      c.width = v.videoWidth; c.height = v.videoHeight;
      c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
      return new Promise(function (resolve) { c.toBlob(resolve, "image/jpeg", quality); });
    }).then(function (blob) {
      URL.revokeObjectURL(url);
      if (!blob) throw new Error("Could not encode the key photo");
      return blob;
    }, function (e) {
      URL.revokeObjectURL(url);
      throw e.message === "decode" ? new Error("This browser can't decode the video to grab the key photo. " +
        "Pass your own JPEG as options.photo, or try another browser.") : e;
    });
  }

  function readBlob(blob) {
    if (blob.arrayBuffer) return blob.arrayBuffer().then(function (b) { return new Uint8Array(b); });
    return new Promise(function (resolve, reject) {
      var r = new FileReader();
      r.onload = function () { resolve(new Uint8Array(r.result)); };
      r.onerror = function () { reject(r.error); };
      r.readAsArrayBuffer(blob);
    });
  }

  /**
   * Make a Live Photo from a video File/Blob (MP4 or MOV with H.264/HEVC).
   * options.stillTime   key photo time in seconds (default: middle of the video)
   * options.photo       a JPEG Blob to use as the key photo instead of a video frame
   * options.jpegQuality 0..1 for the grabbed frame (default 1)
   * Resolves to {photo: Blob, video: Blob, contentId, stillTime, names: {photo, video}}.
   */
  function makeLivePhoto(file, options) {
    var o = options || {};
    var when = new Date(), contentId = o.contentId || uuid();
    return readBlob(file).then(function (bytes) {
      var mov = buildLiveMov(bytes, { stillTime: o.stillTime, contentId: contentId, creationDate: when });
      var still = o.photo ? Promise.resolve(o.photo) : grabFrame(file, mov.stillTime, o.jpegQuality == null ? 1 : o.jpegQuality);
      return still.then(readBlob).then(function (jpeg) {
        var base = ((file.name || "video").replace(/\.[^.]+$/, "")) + "_live";
        return {
          photo: new Blob([addLivePhotoExif(jpeg, contentId, when)], { type: "image/jpeg" }),
          video: new Blob(mov.parts, { type: "video/quicktime" }),
          contentId: contentId,
          stillTime: mov.stillTime,
          names: { photo: base + ".JPG", video: base + ".MOV" }
        };
      });
    });
  }

  return {
    makeLivePhoto: makeLivePhoto,
    buildLiveMov: buildLiveMov,
    addLivePhotoExif: addLivePhotoExif,
    readVideoInfo: readVideoInfo,
    version: "1.0.0"
  };
});

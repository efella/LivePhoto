# Live Photo Maker

Turn any short video, or even a single photo, into a real **iPhone Live Photo**: a still
picture that comes alive when you press on it.

- **No quality loss.** The video is copied bit-for-bit, never re-compressed, and the
  still is the exact frame you choose.
- **Free and open source.** No watermark, no account, no subscription.
- **Desktop app** for Windows, macOS and Linux, plus a **command line** for batches.
- **Website version.** A small JavaScript file that does everything in the browser, so
  nothing gets uploaded. Put it on your own site in a few lines, or
  [try it online](https://efella.github.io/LivePhoto/web/).

> Not affiliated with Apple. "Live Photos" and "iPhone" are trademarks of Apple Inc.

---

## Contents

- [Quick start](#quick-start)
- [Using the app](#using-the-app)
- [Getting it onto your iPhone](#getting-it-onto-your-iphone)
- [Command line](#command-line)
- [Put it on your website](#put-it-on-your-website)
- [What you need](#what-you-need)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [License](#license)

---

## Quick start

1. Download this repository: **Code → Download ZIP**, then unzip it. Or clone it:
   `git clone https://github.com/efella/LivePhoto.git`
2. Install **Python 3.8 or newer** from [python.org](https://www.python.org/downloads/).
   - **Windows:** tick **"Add python.exe to PATH"** in the installer.
   - **macOS:** use the python.org installer. The `python3` built into macOS is too old
     to show windows (see [Troubleshooting](#troubleshooting)).
3. Start the app:

   | System  | Do this |
   |---------|---------|
   | Windows | Double-click **`Live Photo Maker.bat`**, or drag a video onto it. |
   | macOS   | Double-click **`Live Photo Maker.command`**. If macOS blocks it, right-click it → **Open**. |
   | Linux   | Run `pip install -r requirements.txt`, then `python3 livephoto.py`. |

The first start takes a minute. The launcher creates a private `.venv` folder next to
the app and installs what it needs into it, without touching the rest of your system.
After that it opens straight away.

## Using the app

1. **Open a video or photo.** Click **Open…**, click the big empty area, or drag a file
   onto the window.
2. **Pick the key photo.** This is the still image Photos shows before you press it.
   - Click anywhere on the film strip, or drag the white marker.
   - Use **← / →** to step one frame at a time.
3. **Trim (optional).** Drag the blue edges of the strip. iPhone Live Photos are about 3
   seconds long.
   - Trimming means the video has to be re-encoded, which is very high quality but no
     longer bit-for-bit. The green or yellow label under the preview always tells you
     which mode you're in.
4. Click **Make Live Photo.** You get two files next to the original:
   `name_live.JPG` and `name_live.MOV`. Together they are one Live Photo.
5. On a Mac, click **Add to Photos** to import it straight into the Photos app.

**Photos as input.** If you open a photo instead of a video, the app makes a 3-second
clip that gently zooms out to your full picture and back in. Your original JPEG is used
untouched as the key photo. Use the **Zoom** slider to set how much it moves.

## Getting it onto your iPhone

A Live Photo is **two files that belong together**. The iPhone only treats them as a
Live Photo if they arrive together, through a route that understands Live Photos.

| Route | Result |
|---|---|
| **Mac → Photos app**: drag both files in at once, or click **Add to Photos** | ✅ Works. With iCloud Photos on, it syncs to your iPhone. |
| **Mac → AirDrop** the imported Live Photo from Photos to your iPhone | ✅ Works |
| **Website version on the iPhone** → **Share / Save…** → *Save 2 Items* | 🧪 Experimental, please report back |
| Uploading on **icloud.com** | ❌ Arrives as a separate photo and video |
| **iCloud for Windows** upload folder | ❌ Arrives as a separate photo and video |

> **It shows LIVE but doesn't move?** That's normal for the first few minutes. The badge
> arrives before the video part has finished downloading to the phone. Until it has,
> pressing does nothing and Live / Loop / Bounce are greyed out. Wait a little and try
> again.

## Command line

```bash
# video: default key photo = middle of the clip
python livephoto.py video.mp4 --cli

# choose the key photo (seconds), trim, and pick the output folder
python livephoto.py video.mp4 --cli --still 1.2 --start 0.5 --duration 3 -o out/

# photo: make a zooming Live Photo (zoom in percent)
python livephoto.py photo.jpg --cli --zoom 15

# macOS: also import into the Photos app
python livephoto.py video.mp4 --cli --add-to-photos
```

Run `python livephoto.py --help` for all options. Existing files are never overwritten:
a second run writes `name_live2.*`.

You can also use it from your own Python code:

```python
import livephoto
jpg, mov = livephoto.convert("video.mp4", out_dir="out", still_time=1.5)
jpg, mov = livephoto.convert_photo("photo.jpg", out_dir="out", zoom=12)
```

## Put it on your website

[`web/livephoto.js`](web/livephoto.js) is a single file with no dependencies. It builds
the Live Photo **entirely in the visitor's browser**: no server, no upload, no ffmpeg.
[`web/index.html`](web/index.html) is a complete demo page you can host as-is, for
example on GitHub Pages, or copy from. It runs live at
**[efella.github.io/LivePhoto/web](https://efella.github.io/LivePhoto/web/)**.

Opening `web/index.html` straight from your disk works too.

### Minimal example

```html
<input type="file" id="pick" accept="video/mp4,video/quicktime">
<script src="livephoto.js"></script>
<script>
  document.getElementById("pick").onchange = async (e) => {
    const file = e.target.files[0];
    const live = await LivePhotoMaker.makeLivePhoto(file, { stillTime: 1.5 });

    // live.photo and live.video are Blobs; offer both for download
    for (const [blob, name] of [[live.photo, live.names.photo], [live.video, live.names.video]]) {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
    }
  };
</script>
```

### API

| Function | What it does |
|---|---|
| `makeLivePhoto(file, options)` | Promise → `{ photo, video, contentId, stillTime, names }`. `photo` is a JPEG Blob, `video` a QuickTime Blob. |
| `buildLiveMov(bytes, { stillTime, contentId })` | The MOV half only, from a `Uint8Array`. Returns `{ parts }`; `new Blob(parts)` is the file. Also works in Node.js. |
| `addLivePhotoExif(jpegBytes, contentId)` | Adds the Live Photo tag to any JPEG, returned as a `Uint8Array`. Also works in Node.js. |
| `readVideoInfo(bytes)` | `{ duration, codec, copyable, ... }`, so you can check a file before converting. |

`makeLivePhoto` options:

| Option | Meaning |
|---|---|
| `stillTime` | Key photo time in seconds. Default: the middle of the video. |
| `photo` | Your own JPEG Blob to use as the key photo instead of a video frame. |
| `jpegQuality` | 0 to 1, used for the grabbed frame. Default: 1. |

**Limits of the web version:**
- It takes MP4 / MOV files with **H.264 or HEVC** video, which covers phones, most cameras
  and editors.
- It can't trim or re-encode. Use the desktop app for other formats or for trimming.
- The video is copied bit-for-bit. The key photo is captured by the browser, which is
  very high quality but slightly below the desktop app (about 39 dB vs 57 dB PSNR in our
  tests). Pass your own `photo` if that matters to you.
- The browser has to be able to play the video to grab the frame. HEVC doesn't play in
  every browser.

Node.js works too, with the same file:
`const LivePhotoMaker = require("./web/livephoto.js")`, then `buildLiveMov` and
`addLivePhotoExif`.

## What you need

| | Required | Why |
|---|---|---|
| **Python 3.8+** with Tk 8.6 or newer | yes | The app is plain Python. Tk draws the window. |
| **ffmpeg** | yes, installed for you | Reads and copies the video. Comes from the `imageio-ffmpeg` package, or uses `ffmpeg` if it's already on your PATH. |
| **Pillow** | yes, installed for you | Saves the key photo, reads photos, draws the previews. |
| **tkinterdnd2** | optional | Lets you drag files onto the window. The launchers install it if they can. |

The launchers install everything except Python. To set it up by hand:

```bash
pip install -r requirements.txt
pip install tkinterdnd2   # optional
```

The website version needs nothing at all.

## How it works

An iPhone Live Photo is a **JPEG (or HEIC) photo** plus a **QuickTime .MOV video**. iOS
links the two through a shared random ID, the *content identifier*, and a marker that
says which moment of the video the photo shows:

```
 name_live.JPG                          name_live.MOV
┌───────────────────────────┐         ┌─────────────────────────────────────────────┐
│ EXIF                      │         │ video + audio  (copied bit-for-bit)          │
│  └ Apple MakerNote        │         │ moov/meta                                    │
│     └ tag 17 = 8B36…FA97 ◄┼── same ─┼─► content.identifier = 8B36…FA97           │
│ the key photo             │   ID    │ timed metadata track                         │
└───────────────────────────┘         │  └ still-image-time  @ 1.47 s  ◄ key photo   │
                                      └─────────────────────────────────────────────┘
```

**The photo.** Live Photo Maker writes a minimal EXIF block containing an **Apple
MakerNote** (`"Apple iOS"` header, big-endian). Tag `0x11` holds the content identifier.
The MakerNote is padded to 1568 bytes, the size an iPhone writes, because some versions
of Apple's ImageIO ignore MakerNotes smaller than about 1 KB.

**The video:**

1. ffmpeg **remuxes** the input into a QuickTime `.MOV` with `-c:v copy`: the
   compressed video is copied, not decoded, so nothing is lost. The ID goes in as the
   `com.apple.quicktime.content.identifier` metadata key.
2. The program then edits the MOV's boxes directly (`add_still_image_time` in
   [`livephoto.py`](livephoto.py)):
   - It moves the metadata from ISO-style `moov/udta/meta` to QuickTime-style
     `moov/meta`, where iPhones keep it.
   - It adds a **timed metadata track**: handler `meta`, sample entry `mebx`, key
     `com.apple.quicktime.still-image-time`, type int8.
     - The track has one sample with the value `-1`, positioned at the key photo time
       with an empty edit.
     - A `tref/cdsc` reference links it to the video track.
   - It marks every track as enabled, in movie, in preview and in poster, as iPhone
     files do.

The result was checked against real iPhone Live Photos and with Apple's own frameworks:
ImageIO, AVFoundation, and PhotoKit's `PHLivePhoto`, which returns nothing for a
mismatched pair. Imported into Photos on a Mac, it becomes one Live Photo that plays and
syncs to the iPhone.

**From a photo:** ffmpeg renders a 3-second H.264 clip from the picture, using a smooth
cosine zoom with the full photo exactly at the middle frame. The original JPEG becomes
the key photo unchanged. If the photo has to be rotated or converted, it is saved as a
quality-100 JPEG with no chroma subsampling.

**In the browser:** [`web/livephoto.js`](web/livephoto.js) does the same box editing in
JavaScript on the original MP4/MOV, so no ffmpeg is needed.
- It rewrites the chunk offsets, because the `moov` box moves to the end of the file.
- It grabs the key photo with a `<video>` element and a canvas.

## Troubleshooting

**The LIVE badge shows on my iPhone but the photo doesn't move.**
The video part hasn't downloaded to the phone yet. Wait a few minutes.

**It arrived as a separate photo and video.**
The two files took a route that doesn't know about Live Photos, such as icloud.com or the
iCloud for Windows folder. Use one of the ✅ routes in
[Getting it onto your iPhone](#getting-it-onto-your-iphone). Delete the stray copies.

**macOS: the window never appears, or the launcher says it needs Tk 8.6.**
The `python3` that comes with macOS (`/usr/bin/python3`) ships Tk 8.5, which can't open
windows on current macOS. Install Python from [python.org](https://www.python.org/downloads/)
and delete the `.venv` folder so the launcher sets itself up again. With Homebrew, run
`brew install python python-tk`.

**Windows: "Python 3 was not found".**
Install Python from python.org and tick **"Add python.exe to PATH"**. The Microsoft
Store shortcut that Windows offers instead doesn't count.

**The label says "Re-encode".**
Either you trimmed the clip, or the video codec isn't one iPhones play (VP9, AV1, …).
The app then encodes H.264 at CRF 12, which is visually lossless. Keep the full length of
an H.264 / HEVC video if you want the bit-for-bit copy.

**Drag and drop doesn't work.**
Run `pip install tkinterdnd2`, or use **Open…** instead.

**Something else.**
Click **Show details** at the bottom of the app to see the full log, and include it when
you [open an issue](https://github.com/efella/LivePhoto/issues).

## Project layout

| File | What it is |
|---|---|
| `livephoto.py` | The engine and the command line: ffmpeg handling, EXIF, MOV box editing |
| `livephoto_gui.py` | The desktop app (tkinter) |
| `Live Photo Maker.bat` / `.command` | Double-click launchers for Windows / macOS |
| `requirements.txt` | Python packages |
| `web/livephoto.js` | The browser and Node.js library |
| `web/index.html` | The demo web page |

## Credits

- [ExifTool](https://exiftool.org/TagNames/Apple.html) for its documentation of Apple's
  MakerNote and QuickTime tags.
- [MotionPhotoConverter](https://github.com/lutao043/MotionPhotoConverter)'s technical
  notes, for the MakerNote size finding.

## License

[MIT](LICENSE). Use it, change it, ship it.

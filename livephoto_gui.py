"""
Live Photo Maker - desktop app (tkinter). Start it with:  python livephoto.py
"""

import io
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import livephoto as lp

try:
    from PIL import Image, ImageEnhance, ImageTk
except ImportError:
    Image = None

C = {
    "bg": "#f5f5f7", "card": "#ffffff", "text": "#1d1d1f", "muted": "#6e6e73", "border": "#d2d2d7",
    "trough": "#e5e5ea", "accent": "#0a84ff", "accent_hover": "#0071e3", "accent_off": "#9cc9ff",
    "good": "#1a7f37", "good_bg": "#dcf5e3", "warn": "#8a5a00", "warn_bg": "#fff3cd",
}
MIN_CLIP = 0.5  # seconds
FILETYPES = [("Videos and photos", " ".join("*" + e for e in lp.VIDEO_EXTS + lp.IMAGE_EXTS)),
             ("Videos", " ".join("*" + e for e in lp.VIDEO_EXTS)),
             ("Photos", " ".join("*" + e for e in lp.IMAGE_EXTS)),
             ("All files", "*.*")]

TRANSFER_HELP = """Both files have to reach your iPhone together, through a route that understands Live Photos.

Works:
  •  Mac: drag both files into the Photos app at the same time (or click "Add to Photos"). With iCloud Photos on, it syncs to your iPhone.
  •  Mac: after importing, AirDrop it from Photos to your iPhone.

Doesn't work:
  •  Uploading on icloud.com, or the iCloud for Windows upload folder: they split it into a separate photo and video.

Tip: on the iPhone the LIVE badge appears before the video part has downloaded. If it doesn't move yet (and Loop / Bounce are greyed out), give it a few minutes."""


def _fmt(t):
    return f"{t:.2f} s"


def _open_folder(path):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def _round_rect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


class App:
    def __init__(self, root, dnd_files=None):
        self.root = root
        self.dnd = dnd_files
        self.src = None
        self.kind = None          # "video" or "photo"
        self.info = None
        self.dur = 0.0
        self.start = self.end = self.key = 0.0
        self.preview = None       # PIL image shown in the big preview
        self.photo_keeps_original = False
        self.strip = None         # PIL filmstrip for the timeline
        self._strip_cache = None  # (width, normal, dimmed)
        self._tk_imgs = {}
        self._drag = None
        self._preview_job = None
        self._preview_gen = 0
        self.busy = False
        self.result = None
        self.q = queue.Queue()

        s = max(1.0, root.winfo_fpixels("1i") / 96.0)  # Windows display scaling
        self.px = lambda n: int(round(n * s))
        self._fonts()
        self._style()
        self._build()
        root.after(50, self._poll)

    # ------------------------------------------------------------------ setup

    def _fonts(self):
        base = tkfont.nametofont("TkDefaultFont").actual("family")
        bump = 3 if sys.platform == "darwin" else 0  # macOS point sizes render smaller
        self.f_title = tkfont.Font(family=base, size=17 + bump, weight="bold")
        self.f_h = tkfont.Font(family=base, size=10 + bump, weight="bold")
        self.f_body = tkfont.Font(family=base, size=10 + bump)
        self.f_small = tkfont.Font(family=base, size=9 + bump)
        self.f_badge = tkfont.Font(family=base, size=8 + bump, weight="bold")
        self.f_button = tkfont.Font(family=base, size=11 + bump, weight="bold")

    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=C["bg"], foreground=C["text"], font=self.f_body)
        st.configure("TFrame", background=C["bg"])
        st.configure("Card.TFrame", background=C["card"])
        st.configure("TLabel", background=C["bg"], foreground=C["text"])
        st.configure("Muted.TLabel", foreground=C["muted"], font=self.f_small)
        st.configure("Title.TLabel", font=self.f_title)
        st.configure("H.TLabel", font=self.f_h)
        st.configure("Card.TLabel", background=C["card"])
        st.configure("CardMuted.TLabel", background=C["card"], foreground=C["muted"], font=self.f_small)
        st.configure("CardH.TLabel", background=C["card"], font=self.f_h)
        st.configure("Good.TLabel", background=C["card"], foreground=C["good"], font=self.f_h)
        st.configure("TButton", padding=(self.px(12), self.px(6)), background=C["card"], foreground=C["text"],
                     bordercolor=C["border"], lightcolor=C["card"], darkcolor=C["card"], focuscolor=C["card"])
        st.map("TButton", background=[("active", "#ececf0"), ("disabled", C["bg"])],
               foreground=[("disabled", C["muted"])])
        st.configure("Accent.TButton", padding=(self.px(16), self.px(11)), background=C["accent"], foreground="white",
                     bordercolor=C["accent"], lightcolor=C["accent"], darkcolor=C["accent"],
                     focuscolor=C["accent"], font=self.f_button)
        st.map("Accent.TButton", background=[("disabled", C["accent_off"]), ("active", C["accent_hover"])],
               foreground=[("disabled", "white")], bordercolor=[("disabled", C["accent_off"])],
               lightcolor=[("disabled", C["accent_off"]), ("active", C["accent_hover"])],
               darkcolor=[("disabled", C["accent_off"]), ("active", C["accent_hover"])])
        st.configure("Link.TButton", padding=(0, 0), background=C["bg"], foreground=C["accent"],
                     bordercolor=C["bg"], lightcolor=C["bg"], darkcolor=C["bg"], focuscolor=C["bg"], font=self.f_small)
        st.map("Link.TButton", background=[("active", C["bg"])], foreground=[("active", C["accent_hover"])])
        st.configure("TEntry", fieldbackground=C["card"], bordercolor=C["border"], lightcolor=C["border"],
                     darkcolor=C["border"], padding=self.px(5))
        st.configure("Horizontal.TProgressbar", background=C["accent"], troughcolor=C["trough"],
                     bordercolor=C["trough"], lightcolor=C["accent"], darkcolor=C["accent"])
        st.configure("Horizontal.TScale", background=C["accent"], troughcolor=C["trough"],
                     bordercolor=C["trough"], lightcolor=C["accent"], darkcolor=C["accent"])

    def _build(self):
        px = self.px
        root = self.root
        root.title(lp.APP_NAME)
        root.configure(background=C["bg"])
        root.minsize(px(600), px(680))
        root.geometry(f"{px(720)}x{px(820)}")

        outer = ttk.Frame(root, padding=(px(20), px(16), px(20), px(14)))
        outer.pack(fill="both", expand=True)
        self.outer = outer

        # Header
        head = ttk.Frame(outer)
        head.pack(fill="x")
        titles = ttk.Frame(head)
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text=lp.APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Turn a video or a photo into an iPhone Live Photo, without losing quality.",
                  style="Muted.TLabel").pack(anchor="w")
        ttk.Button(head, text="Open…", command=self.pick).pack(side="right", anchor="n")

        # Preview
        self.canvas = tk.Canvas(outer, background=C["card"], highlightthickness=1,
                                highlightbackground=C["border"], height=px(330), cursor="hand2")
        self.canvas.pack(fill="both", expand=True, pady=(px(14), px(8)))
        self.canvas.bind("<Configure>", lambda e: self._draw_preview())
        self.canvas.bind("<Button-1>", lambda e: None if self.src else self.pick())

        # Info line
        info = ttk.Frame(outer)
        info.pack(fill="x")
        self.info_lbl = ttk.Label(info, text="", style="Muted.TLabel")
        self.info_lbl.pack(side="left")
        self.chip = tk.Label(info, text="", font=self.f_badge, padx=px(8), pady=px(2), borderwidth=0)

        # Editor area: timeline (video) or motion (photo)
        self.editor = ttk.Frame(outer)
        self.editor.pack(fill="x", pady=(px(10), 0))

        self.timeline = ttk.Frame(self.editor)
        self.tl = tk.Canvas(self.timeline, height=px(82), background=C["bg"], highlightthickness=0, cursor="hand2")
        self.tl.pack(fill="x")
        self.tl.bind("<Configure>", lambda e: self._draw_timeline())
        self.tl.bind("<ButtonPress-1>", self._tl_press)
        self.tl.bind("<B1-Motion>", self._tl_drag)
        self.tl.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        row = ttk.Frame(self.timeline)
        row.pack(fill="x")
        self.trim_lbl = ttk.Label(row, text="", style="Muted.TLabel")
        self.trim_lbl.pack(side="left")
        self.key_lbl = ttk.Label(row, text="", style="H.TLabel")
        self.key_lbl.pack(side="right")
        ttk.Label(self.timeline, style="Muted.TLabel",
                  text="Click or drag on the strip to pick the key photo  ·  ← / → step one frame  ·  "
                       "drag the blue edges to trim").pack(anchor="w", pady=(px(2), 0))

        self.motion = ttk.Frame(self.editor)
        mrow = ttk.Frame(self.motion)
        mrow.pack(fill="x")
        ttk.Label(mrow, text="Zoom", style="H.TLabel").pack(side="left")
        self.zoom_var = tk.DoubleVar(value=lp.DEFAULT_ZOOM)
        self.zoom_lbl = ttk.Label(mrow, text="", style="H.TLabel", width=5, anchor="e")
        self.zoom_lbl.pack(side="right")
        ttk.Scale(mrow, from_=3, to=25, variable=self.zoom_var,
                  command=lambda v: self.zoom_lbl.config(text=f"{float(v):.0f} %")).pack(
            side="left", fill="x", expand=True, padx=px(12))
        self.zoom_lbl.config(text=f"{lp.DEFAULT_ZOOM} %")
        ttk.Label(self.motion, style="Muted.TLabel",
                  text=f"A {lp.PHOTO_CLIP_SECONDS:.0f}-second clip eases out of a zoom to your full photo and back in. "
                       "Your photo stays the key photo.").pack(anchor="w", pady=(px(4), 0))

        # Output folder
        out = ttk.Frame(outer)
        out.pack(fill="x", pady=(px(14), 0))
        ttk.Label(out, text="Save to", style="H.TLabel").pack(side="left")
        self.out_var = tk.StringVar()
        ttk.Entry(out, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=px(10))
        ttk.Button(out, text="Change…", command=self.pick_out).pack(side="left")

        # Action
        self.go = ttk.Button(outer, text="Make Live Photo", style="Accent.TButton", command=self.make)
        self.go.pack(fill="x", pady=(px(14), 0))
        self.progress = ttk.Progressbar(outer, mode="indeterminate")

        # Result card
        self.result_card = ttk.Frame(outer, style="Card.TFrame", padding=px(12))
        top = ttk.Frame(self.result_card, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="✓  Live Photo ready", style="Good.TLabel").pack(side="left")
        self.result_lbl = ttk.Label(self.result_card, text="", style="CardMuted.TLabel")
        self.result_lbl.pack(anchor="w", pady=(px(2), px(8)))
        btns = ttk.Frame(self.result_card, style="Card.TFrame")
        btns.pack(fill="x")
        ttk.Button(btns, text="Show files", command=lambda: _open_folder(self.result[0])).pack(side="left")
        if sys.platform == "darwin":
            self.photos_btn = ttk.Button(btns, text="Add to Photos", command=self.add_to_photos)
            self.photos_btn.pack(side="left", padx=(px(8), 0))
        ttk.Button(btns, text="How do I get it on my iPhone?", command=self.show_help).pack(side="right")

        # Details / log
        foot = ttk.Frame(outer)
        foot.pack(fill="x", side="bottom", pady=(px(8), 0))
        self.details_btn = ttk.Button(foot, text="Show details", style="Link.TButton", command=self.toggle_details)
        self.details_btn.pack(side="left")
        ttk.Label(foot, text=f"v{lp.__version__}", style="Muted.TLabel").pack(side="right")
        self.log_box = tk.Text(outer, height=7, state="disabled", relief="flat", background=C["card"],
                               foreground=C["text"], font=("Consolas", 9) if sys.platform == "win32" else self.f_small,
                               highlightthickness=1, highlightbackground=C["border"], padx=px(8), pady=px(6))

        # Keyboard + drag and drop
        root.bind("<Left>", lambda e: self._step(-1))
        root.bind("<Right>", lambda e: self._step(1))
        root.bind("<Control-o>", lambda e: self.pick())
        root.bind("<Command-o>" if sys.platform == "darwin" else "<Control-O>", lambda e: self.pick())
        if self.dnd:
            for w in (self.canvas, self.outer):
                w.drop_target_register(self.dnd)
                w.dnd_bind("<<Drop>>", self._on_drop)

        self._draw_preview()

    # ------------------------------------------------------------------ threading

    def _poll(self):
        """Run callbacks queued by worker threads on the Tk thread."""
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _later(self, fn):
        self.q.put(fn)

    def log(self, msg):
        def write():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self._later(write)

    # ------------------------------------------------------------------ loading

    def pick(self):
        if self.busy:
            return
        p = filedialog.askopenfilename(title="Choose a video or photo", filetypes=FILETYPES)
        if p:
            self.load(p)

    def pick_out(self):
        d = filedialog.askdirectory(title="Save Live Photos to", initialdir=self.out_var.get() or None)
        if d:
            self.out_var.set(d)

    def _on_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        if files and not self.busy:
            self.load(files[0])

    def load(self, path):
        if Image is None:
            messagebox.showerror(lp.APP_NAME, "This app needs Pillow.\n\nRun:  pip install pillow")
            return
        if not os.path.isfile(path):
            return
        self.src, self.preview, self.strip, self._strip_cache = path, None, None, None
        self.result_card.pack_forget()
        self.out_var.set(os.path.dirname(os.path.abspath(path)))
        self.info_lbl.config(text=f"Loading {os.path.basename(path)}…")
        self.chip.pack_forget()
        self.timeline.pack_forget()
        self.motion.pack_forget()
        self._draw_preview()
        kind = "photo" if lp.is_image(path) else "video"

        def work():
            try:
                if kind == "photo":
                    img, original = lp.load_photo(path)
                    size = img.size
                    img.thumbnail((1600, 1600))
                    self._later(lambda: self._loaded_photo(path, img, size, original is not None))
                else:
                    info = lp.probe(path)
                    self._later(lambda: self._loaded_video(path, info))
            except Exception as ex:
                msg = str(ex)
                self._later(lambda: self._load_failed(path, msg))
        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, path, msg):
        if path != self.src:
            return
        self.src = None
        self.info_lbl.config(text="")
        self._draw_preview()
        messagebox.showerror(lp.APP_NAME, f"Couldn't open {os.path.basename(path)}:\n\n{msg}")

    def _loaded_photo(self, path, img, size, keeps_original):
        if path != self.src:
            return
        self.kind, self.preview, self.photo_keeps_original = "photo", img, keeps_original
        self.info_lbl.config(text=f"{os.path.basename(path)}  ·  photo  ·  {size[0]}×{size[1]}")
        self._set_chip("Original kept as the key photo" if keeps_original else "Key photo saved as JPEG q100",
                       good=keeps_original)
        self.motion.pack(fill="x")
        self._draw_preview()

    def _loaded_video(self, path, info):
        if path != self.src:
            return
        self.kind, self.info = "video", info
        self.dur = info["duration"] or 0.0
        self.start, self.end = 0.0, self.dur
        self.key = self._snap(self.dur / 2)
        codec = {"h264": "H.264", "hevc": "HEVC"}.get(info["vcodec"], (info["vcodec"] or "?").upper())
        parts = [os.path.basename(path), codec]
        if info["width"]:
            parts.append(f"{info['width']}×{info['height']}")
        if info["fps"]:
            parts.append(f"{info['fps']:g} fps")
        parts.append(_fmt(self.dur))
        text = "  ·  ".join(parts)
        if self.dur > 5:
            text += "   (iPhone Live Photos are ~3 s; consider trimming)"
        self.info_lbl.config(text=text)
        self.timeline.pack(fill="x")
        self._update_mode()
        self._draw_timeline()
        self._schedule_preview()

        def strip_work():
            try:
                w = max(self.tl.winfo_width(), self.px(400))
                th = self.px(52)
                aspect = (info["width"] / info["height"]) if info["width"] and info["height"] else 16 / 9
                count = max(6, min(24, int(round(w / (th * aspect)))))
                png = lp.extract_filmstrip_png(path, self.dur, count, th)
                img = Image.open(io.BytesIO(png)).convert("RGB")
                self._later(lambda: self._got_strip(path, img))
            except Exception as ex:
                self.log(f"Thumbnails unavailable: {ex}")
        threading.Thread(target=strip_work, daemon=True).start()

    def _got_strip(self, path, img):
        if path == self.src:
            self.strip, self._strip_cache = img, None
            self._draw_timeline()

    # ------------------------------------------------------------------ preview

    def _schedule_preview(self):
        if self._preview_job:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(120, self._load_preview_frame)

    def _load_preview_frame(self):
        self._preview_job = None
        if self.kind != "video":
            return
        self._preview_gen += 1
        gen, src, t = self._preview_gen, self.src, self.key

        def work():
            try:
                png = lp.extract_frame_png(src, t, max_height=900)
                img = Image.open(io.BytesIO(png)).convert("RGB")
                self._later(lambda: self._got_preview(gen, img))
            except Exception as ex:
                self.log(str(ex))
        threading.Thread(target=work, daemon=True).start()

    def _got_preview(self, gen, img):
        if gen == self._preview_gen:
            self.preview = img
            self._draw_preview()

    def _draw_preview(self):
        c, px = self.canvas, self.px
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 20 or h < 20:
            return
        cursor = "" if self.src else "hand2"
        if c.cget("cursor") != cursor:  # reconfiguring fires <Configure>, which redraws: only when it changes
            c.configure(cursor=cursor)
        if not self.src:
            m = px(18)
            c.create_rectangle(m, m, w - m, h - m, outline=C["border"], width=2, dash=(6, 4))
            cy = h / 2
            # a little "Live Photo" glyph: concentric rings
            for r, wd in ((px(22), 2), (px(14), 2), (px(6), 0)):
                c.create_oval(w / 2 - r, cy - px(46) - r, w / 2 + r, cy - px(46) + r,
                              outline=C["accent"], width=wd, fill=C["accent"] if wd == 0 else "")
            c.create_text(w / 2, cy + px(6), font=self.f_h, fill=C["text"],
                          text="Drop a video or photo here" if self.dnd else "Click to choose a video or photo")
            c.create_text(w / 2, cy + px(30), font=self.f_small, fill=C["muted"],
                          text="or click to browse  ·  MP4, MOV, MKV, WEBM, JPG, PNG…" if self.dnd
                          else "MP4, MOV, MKV, WEBM, JPG, PNG…")
            return
        if self.preview is None:
            c.create_text(w / 2, h / 2, text="Loading…", font=self.f_body, fill=C["muted"])
            return
        im = self.preview
        pad = px(14)
        scale = min((w - 2 * pad) / im.width, (h - 2 * pad) / im.height)
        size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
        tkimg = ImageTk.PhotoImage(im.resize(size, Image.LANCZOS))
        self._tk_imgs["preview"] = tkimg
        x0, y0 = (w - size[0]) // 2, (h - size[1]) // 2
        c.create_image(x0, y0, image=tkimg, anchor="nw")
        # iOS-style LIVE badge
        bx, by = x0 + px(10), y0 + px(10)
        _round_rect(c, bx, by, bx + px(58), by + px(22), px(8), fill="#ffffff", outline="")
        cx, cy = bx + px(13), by + px(11)
        for r in (px(6), px(3)):
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=C["text"], width=1)
        c.create_text(bx + px(38), cy, text="LIVE", font=self.f_badge, fill=C["text"])

    # ------------------------------------------------------------------ timeline

    def _geom(self):
        pad = self.px(10)
        w = max(self.tl.winfo_width(), 2 * pad + 10)
        return pad, w - 2 * pad, self.px(10), self.px(52)  # x0, width, y0, strip height

    def _t2x(self, t):
        x0, sw, _, _ = self._geom()
        return x0 + (t / self.dur) * sw if self.dur else x0

    def _x2t(self, x):
        x0, sw, _, _ = self._geom()
        return min(max((x - x0) / sw * self.dur, 0.0), self.dur) if sw else 0.0

    def _snap(self, t):
        fps = self.info and self.info.get("fps")
        return round(t * fps) / fps if fps else t

    def _draw_timeline(self):
        c = self.tl
        c.delete("all")
        if self.kind != "video" or not self.dur:
            return
        px = self.px
        x0, sw, y0, sh = self._geom()
        xs, xe, xk = self._t2x(self.start), self._t2x(self.end), self._t2x(self.key)
        if self.strip is not None and sw > 10:
            if not self._strip_cache or self._strip_cache[0] != sw:
                normal = self.strip.resize((sw, sh), Image.LANCZOS)
                dim = ImageEnhance.Brightness(normal).enhance(0.45)
                self._strip_cache = (sw, normal, dim)
            _, normal, dim = self._strip_cache
            self._tk_imgs["dim"] = ImageTk.PhotoImage(dim)
            c.create_image(x0, y0, image=self._tk_imgs["dim"], anchor="nw")
            a, b = int(xs - x0), int(xe - x0)
            if b - a > 1:
                self._tk_imgs["sel"] = ImageTk.PhotoImage(normal.crop((a, 0, b, sh)))
                c.create_image(x0 + a, y0, image=self._tk_imgs["sel"], anchor="nw")
        else:
            c.create_rectangle(x0, y0, x0 + sw, y0 + sh, fill=C["trough"], outline="")
        # selection frame + trim handles
        c.create_rectangle(xs, y0 - 2, xe, y0 + sh + 2, outline=C["accent"], width=3)
        hw = px(5)
        for x in (xs, xe):
            _round_rect(c, x - hw, y0 - px(3), x + hw, y0 + sh + px(3), px(3), fill=C["accent"], outline="")
            c.create_line(x, y0 + sh / 2 - px(7), x, y0 + sh / 2 + px(7), fill="white", width=2)
        # key photo marker
        c.create_line(xk, y0 - px(4), xk, y0 + sh + px(4), fill=C["text"], width=px(4))
        c.create_line(xk, y0 - px(4), xk, y0 + sh + px(4), fill="white", width=max(2, px(2)))
        r = px(7)
        cy = y0 + sh + px(12)
        c.create_oval(xk - r, cy - r, xk + r, cy + r, fill="white", outline=C["text"], width=2)

    def _tl_press(self, e):
        if self.kind != "video" or self.busy:
            return
        xs, xe = self._t2x(self.start), self._t2x(self.end)
        grab = self.px(9)
        ds, de = abs(e.x - xs), abs(e.x - xe)
        if min(ds, de) <= grab:
            self._drag = "start" if ds < de or (ds == de and e.x < xs) else "end"
        else:
            self._drag = "key"
        self._tl_drag(e)

    def _tl_drag(self, e):
        if not self._drag or self.busy:
            return
        t = self._x2t(e.x)
        min_clip = min(MIN_CLIP, self.dur)
        old_key = self.key
        if self._drag == "start":
            self.start = max(0.0, min(t, self.end - min_clip))
            if self.start < 0.02:
                self.start = 0.0
        elif self._drag == "end":
            self.end = min(self.dur, max(t, self.start + min_clip))
            if self.end > self.dur - 0.02:
                self.end = self.dur
        else:
            self.key = self._snap(t)
        self.key = min(max(self.key, self.start), self.end)
        self._update_mode()
        self._draw_timeline()
        if self.key != old_key:
            self._schedule_preview()

    def _step(self, frames):
        if self.kind != "video" or self.busy or isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry)):
            return
        fps = (self.info or {}).get("fps") or 30
        self.key = min(max(self._snap(self.key + frames / fps), self.start), self.end)
        self._update_mode()
        self._draw_timeline()
        self._schedule_preview()

    def _trim(self):
        """(start, duration) for convert(), or (None, None) when the full clip is used."""
        if self.start <= 0.01 and self.end >= self.dur - 0.01:
            return None, None
        return (self.start or None), self.end - self.start

    def _update_mode(self):
        start, duration = self._trim()
        clip = self.end - self.start
        self.trim_lbl.config(text=f"Clip  {_fmt(self.start)} – {_fmt(self.end)}   ({_fmt(clip)})")
        self.key_lbl.config(text=f"Key photo  {_fmt(self.key - self.start)}")
        lossless, why = lp.plan_video(self.info, start, duration)
        if lossless:
            self._set_chip("Lossless: video copied bit-for-bit", good=True)
        else:
            self._set_chip(f"Re-encode: {why}", good=False)

    def _set_chip(self, text, good):
        self.chip.config(text=text, foreground=C["good"] if good else C["warn"],
                         background=C["good_bg"] if good else C["warn_bg"])
        self.chip.pack(side="right")

    # ------------------------------------------------------------------ actions

    def make(self):
        if self.busy:
            return
        if not self.src or not self.kind:
            self.pick()
            return
        self.result_card.pack_forget()
        self._set_busy(True)
        self.log(f"— {os.path.basename(self.src)}")
        src, kind, out_dir = self.src, self.kind, self.out_var.get().strip() or None
        zoom = self.zoom_var.get()
        start, duration = self._trim()
        still = self.key - self.start

        def work():
            try:
                if kind == "photo":
                    res = lp.convert_photo(src, out_dir, zoom=zoom, log=self.log)
                else:
                    res = lp.convert(src, out_dir, still_time=still, start=start, duration=duration, log=self.log)
                self._later(lambda: self._done(res))
            except Exception as ex:
                msg = str(ex)
                self.log("ERROR: " + msg)
                self._later(lambda: messagebox.showerror(lp.APP_NAME, msg))
            finally:
                self._later(lambda: self._set_busy(False))
        threading.Thread(target=work, daemon=True).start()

    def _set_busy(self, busy):
        self.busy = busy
        if busy:
            self.go.config(state="disabled", text="Making Live Photo…")
            self.progress.pack(fill="x", pady=(self.px(8), 0), after=self.go)
            self.progress.start(12)
        else:
            self.go.config(state="normal", text="Make Live Photo")
            self.progress.stop()
            self.progress.pack_forget()

    def _done(self, res):
        self.result = res
        jpg, mov = res
        self.result_lbl.config(text=f"{os.path.basename(jpg)}  +  {os.path.basename(mov)}\n"
                                    f"in {os.path.dirname(jpg)}")
        if sys.platform == "darwin":
            self.photos_btn.config(state="normal", text="Add to Photos")
        self.result_card.pack(fill="x", pady=(self.px(12), 0), after=self.go)

    def add_to_photos(self):
        if not self.result:
            return
        self.photos_btn.config(state="disabled", text="Adding…")
        jpg, mov = self.result

        def work():
            try:
                lp.add_to_photos(jpg, mov)
                self.log("Added to Photos as a Live Photo")
                self._later(lambda: self.photos_btn.config(text="Added to Photos ✓"))
                self._later(lambda: messagebox.showinfo(
                    lp.APP_NAME, "Added to Photos as a Live Photo.\n\nWith iCloud Photos on it will show up on your "
                                 "iPhone. Give it a few minutes: until the video part has downloaded it won't move."))
            except Exception as ex:
                msg = str(ex)
                self.log("ERROR: " + msg)
                self._later(lambda: self.photos_btn.config(state="normal", text="Add to Photos"))
                self._later(lambda: messagebox.showerror(lp.APP_NAME, msg))
        threading.Thread(target=work, daemon=True).start()

    def show_help(self):
        messagebox.showinfo("Getting it on your iPhone", TRANSFER_HELP)

    def toggle_details(self):
        if self.log_box.winfo_ismapped():
            self.log_box.pack_forget()
            self.details_btn.config(text="Show details")
        else:
            self.log_box.pack(fill="x", side="bottom", pady=(self.px(8), 0))
            self.details_btn.config(text="Hide details")


def run_gui(initial=None):
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    dnd = None
    try:  # optional drag and drop (pip install tkinterdnd2)
        from tkinterdnd2 import DND_FILES, TkinterDnD
        TkinterDnD.require(root)
        dnd = DND_FILES
    except Exception:
        pass
    app = App(root, dnd)
    if initial:
        root.after(150, lambda: app.load(initial))
    root.mainloop()


if __name__ == "__main__":
    run_gui(sys.argv[1] if len(sys.argv) > 1 else None)

"""
ui.py — Tk toasts and dialogs for YouTube Live Notifier

Threading model
───────────────
Tkinter is not thread-safe, so every widget is created, configured and
destroyed on one persistent UI thread (a hidden Tk root that never closes).
Callers outside this file only use the public functions at the bottom, which
post a job onto _UI_QUEUE and, for dialogs, block on an Event for the result.
Network I/O (thumbnails) stays on worker threads and hands back plain PIL
images; PhotoImages are only created on the UI thread.
"""

import ctypes
import io
import queue
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from ctypes import wintypes
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageOps, ImageTk

from .icons import idle_icon

# ─────────────────────────────────────────────
#  Design tokens
# ─────────────────────────────────────────────
BG        = "#121214"   # dialog header / footer
SURFACE   = "#1a1a1d"   # cards, dialog body
SURFACE2  = "#26262b"   # raised: inputs, hover rows, ghost buttons
BORDER    = "#34343b"
ACCENT    = "#ff2e3c"
INFO      = "#4f8cff"
TEXT      = "#f3f3f5"
TEXT_DIM  = "#9b9ba5"
TEXT_FAINT = "#5f5f69"
SUCCESS   = "#34d399"
DANGER    = "#ff5a5f"
KEY       = "#0e0e10"   # toast window colour-key (outside the rounded card)

FAMILY = "Segoe UI"
F_TITLE  = (FAMILY, 14, "bold")
F_BODY   = (FAMILY, 10)
F_BOLD   = (FAMILY, 10, "bold")
F_SMALL  = (FAMILY, 9)
F_TINY   = (FAMILY, 8)
F_MONO   = ("Consolas", 11)

TOAST_W, TOAST_H = 400, 162
TOAST_PAD = 14          # gap to screen edge / between toasts
TOAST_LIFE = 8000       # ms before auto-dismiss (paused while hovered)
TOAST_MAX = 4
THUMB_W, THUMB_H = 144, 81
TICK = 16               # animation frame, ms


# ─────────────────────────────────────────────
#  Single UI thread
# ─────────────────────────────────────────────
_UI_QUEUE: queue.Queue = queue.Queue()
_UI_ROOT: tk.Tk | None = None
_UI_THREAD: threading.Thread | None = None
_UI_READY = threading.Event()


def _set_dpi_aware():
    """Must run before any Tk window exists."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _dwm(win, attr: int, value: int):
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attr, ctypes.byref(ctypes.c_int(value)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


def _ui_thread_main():
    global _UI_ROOT
    _set_dpi_aware()
    root = tk.Tk()
    root.withdraw()
    root.title("__notifier_root__")
    _UI_ROOT = root

    def _poll():
        try:
            while True:
                fn = _UI_QUEUE.get_nowait()
                try:
                    fn()
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        root.after(50, _poll)

    root.after(50, _poll)
    _UI_READY.set()
    root.mainloop()


def _post(fn):
    """Run fn() on the UI thread (non-blocking), starting the thread if needed."""
    global _UI_THREAD
    if _UI_THREAD is None or not _UI_THREAD.is_alive():
        _UI_READY.clear()
        _UI_THREAD = threading.Thread(target=_ui_thread_main, daemon=True, name="UIThread")
        _UI_THREAD.start()
    _UI_READY.wait(5)
    _UI_QUEUE.put(fn)


# ─────────────────────────────────────────────
#  Pure helpers
# ─────────────────────────────────────────────

def hex_to_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lighten(c: str, amount: int = 22) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(min(255, v + amount) for v in hex_to_rgb(c)))


def _darken(c: str, amount: int = 22) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, v - amount) for v in hex_to_rgb(c)))


def _ellipsize(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _format_viewers(raw: str | None) -> str | None:
    try:
        return f"{int(raw):,} watching" if raw else None
    except ValueError:
        return None


def _format_elapsed(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            dt = datetime.strptime(iso[:-3] + iso[-2:], "%Y-%m-%dT%H:%M:%S%z")
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception as e:
        print("Elapsed time error:", e)
        return None
    if secs < 60:
        return "Just started"
    if secs < 3600:
        return f"Started {secs // 60}m ago"
    h, m = secs // 3600, (secs % 3600) // 60
    return f"Started {h}h {m}m ago" if m else f"Started {h}h ago"


def _parse_handle(text: str) -> str | None:
    """'@name', 'name' or a youtube.com/@name URL -> '@name'; None if invalid."""
    text = text.strip()
    m = re.search(r"youtube\.com/(@[^/?#\s]+)", text, re.I)
    handle = (m.group(1) if m else text).lstrip("@")
    return "@" + handle if re.fullmatch(r"[\w.\-·]{3,30}", handle) else None


def _play_sound():
    try:
        import winsound  # Windows-only
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


def _work_area(root) -> tuple[int, int, int, int]:
    """Screen area minus the taskbar."""
    try:
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _rounded_mask(size: tuple[int, int], radius: int, ss: int = 4) -> Image.Image:
    w, h = size
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius * ss, fill=255)
    return mask.resize(size, Image.LANCZOS)


def _fetch_thumbnail(url: str) -> Image.Image | None:
    """Fetch + crop to the toast thumbnail size. Worker-thread safe (PIL only)."""
    candidates = [url] + ([url.replace("maxresdefault", "hqdefault")] if "maxresdefault" in url else [])
    for u in candidates:
        try:
            r = requests.get(u, timeout=8)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                return ImageOps.fit(img, (THUMB_W, THUMB_H), Image.LANCZOS)
        except Exception as e:
            print(f"Thumbnail fetch error ({u}):", e)
    return None


def _card_image(thumb: Image.Image | None) -> Image.Image:
    """Pre-render the whole toast background: rounded card + border + thumbnail."""
    ss = 3
    W, H = TOAST_W, TOAST_H
    big = Image.new("RGB", (W * ss, H * ss), KEY)
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, W * ss - 1, H * ss - 1), 16 * ss, fill=BORDER)
    d.rounded_rectangle((ss, ss, W * ss - ss - 1, H * ss - ss - 1), 15 * ss, fill=SURFACE)
    card = big.resize((W, H), Image.LANCZOS)

    if thumb is None:   # placeholder: dark tile with a play glyph
        thumb = Image.new("RGB", (THUMB_W, THUMB_H), SURFACE2)
        td = ImageDraw.Draw(thumb)
        cx, cy = THUMB_W // 2, THUMB_H // 2
        td.polygon([(cx - 8, cy - 11), (cx - 8, cy + 11), (cx + 12, cy)], fill=TEXT_FAINT)
    card.paste(thumb, (14, 14), _rounded_mask(thumb.size, 9))
    return card


# ─────────────────────────────────────────────
#  Widgets
# ─────────────────────────────────────────────

def _rrect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle on a canvas (smoothed polygon)."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class _Button(tk.Canvas):
    """Flat rounded button with hover / pressed / disabled states."""
    STYLES = {
        "primary": (ACCENT, "#ffffff"),
        "danger":  (DANGER, "#ffffff"),
        "ghost":   (SURFACE2, TEXT),
    }

    def __init__(self, parent, text, command, style="primary", width=None, height=34, font=F_BOLD, bg=BG):
        w = width or tkfont.Font(font=font).measure(text) + 34
        super().__init__(parent, width=w, height=height, bg=bg, highlightthickness=0, bd=0)
        self._text, self._cmd, self._font = text, command, font
        self._fill, self._fg = self.STYLES[style]
        self._enabled, self._hover, self._down = True, False, False
        self._bw, self._bh = w, height
        self.bind("<Enter>", lambda _: self._set(hover=True))
        self.bind("<Leave>", lambda _: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda _: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _set(self, **kw):
        self._hover = kw.get("hover", self._hover)
        self._down = kw.get("down", self._down)
        self._draw()

    def _release(self, e):
        was_down = self._down
        self._set(down=False)
        if was_down and self._enabled and 0 <= e.x <= self._bw and 0 <= e.y <= self._bh:
            self._cmd()

    def _draw(self):
        self.delete("all")
        fill, fg = (self._fill, self._fg) if self._enabled else (SURFACE2, TEXT_FAINT)
        if self._enabled and self._down:
            fill = _darken(fill)
        elif self._enabled and self._hover:
            fill = _lighten(fill)
        _rrect(self, 1, 1, self._bw - 1, self._bh - 1, 9, fill=fill)
        self.create_text(self._bw / 2, self._bh / 2, text=self._text, fill=fg, font=self._font)
        self.config(cursor="hand2" if self._enabled else "arrow")

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._draw()


def _label(parent, text, font=F_BODY, fg=TEXT, bg=SURFACE, **kw) -> tk.Label:
    return tk.Label(parent, text=text, font=font, fg=fg, bg=bg, **kw)


def _avatar(parent, name: str, bg, size=34) -> tk.Canvas:
    """Circle with the channel's initial, colour derived from the name."""
    palette = ["#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6"]
    colour = palette[sum(map(ord, name)) % len(palette)]
    cv = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
    cv.create_oval(1, 1, size - 1, size - 1, fill=colour, outline="")
    cv.create_text(size / 2, size / 2, text=(name.strip()[:1] or "?").upper(),
                   fill="#ffffff", font=(FAMILY, 11, "bold"))
    return cv


# ─────────────────────────────────────────────
#  Dialog scaffold
# ─────────────────────────────────────────────

class _Dialog:
    """Modal window: header (title/subtitle) · body · footer. Reports one result."""

    def __init__(self, title, subtitle, accent, result_event, result_holder, width=420):
        self._event, self._holder = result_event, result_holder
        self.win = win = tk.Toplevel(_UI_ROOT)
        win.title(title)
        win.overrideredirect(True)          # frameless: we draw our own title bar
        win.attributes("-topmost", True)
        win.configure(bg=BORDER)            # 1px border around the shell
        _dwm(win, 33, 2)                    # rounded corners where DWM supports it (Win11)
        shell = tk.Frame(win, bg=SURFACE)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        # Title bar: app icon · app name · close
        bar = tk.Frame(shell, bg=BG, height=36)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        win._icon = ImageTk.PhotoImage(idle_icon().resize((18, 18), Image.LANCZOS))
        tk.Label(bar, image=win._icon, bg=BG).pack(side="left", padx=(12, 8))
        tk.Label(bar, text="YouTube Live Notifier", font=F_SMALL, fg=TEXT_DIM, bg=BG).pack(side="left")
        close = tk.Label(bar, text="✕", font=F_BODY, fg=TEXT_DIM, bg=BG, width=5, cursor="hand2")
        close.pack(side="right", fill="y")
        close.bind("<Enter>", lambda _: close.config(bg=DANGER, fg="#fff"))
        close.bind("<Leave>", lambda _: close.config(bg=BG, fg=TEXT_DIM))
        close.bind("<Button-1>", lambda _: self.finish(None))
        drag = {}
        for w in (bar, *bar.winfo_children()[:2]):
            w.bind("<ButtonPress-1>", lambda e: drag.update(x=e.x_root - win.winfo_x(), y=e.y_root - win.winfo_y()))
            w.bind("<B1-Motion>", lambda e: win.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}"))

        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x")
        tk.Frame(header, bg=accent, width=4).pack(side="left", fill="y")
        box = tk.Frame(header, bg=BG, padx=16, pady=16)
        box.pack(side="left", fill="x")
        self.title_lbl = _label(box, title, F_TITLE, bg=BG)
        self.title_lbl.pack(anchor="w")
        self.sub_lbl = _label(box, subtitle, F_SMALL, TEXT_DIM, bg=BG)
        self.sub_lbl.pack(anchor="w", pady=(2, 0))

        self.body = tk.Frame(shell, bg=SURFACE, padx=20, pady=16, width=width)
        self.body.pack(fill="both", expand=True)
        self.footer = tk.Frame(shell, bg=BG, padx=20, pady=12)
        self.footer.pack(fill="x")

        win.bind("<Escape>", lambda _: self.finish(None))
        win.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))

    def finish(self, value=None):
        if self._event.is_set():
            return
        self._holder[0] = value
        self._event.set()
        self.win.destroy()

    def button(self, text, command, style="primary", **kw) -> _Button:
        b = _Button(self.footer, text, command, style, bg=BG, **kw)
        b.pack(side="right", padx=(8, 0))
        return b

    def show(self):
        win = self.win
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        l, t, r, b = _work_area(_UI_ROOT)
        win.geometry(f"+{l + (r - l - w) // 2}+{t + (b - t - h) // 2}")
        win.deiconify()
        win.lift()
        win.focus_force()
        win.grab_set()


# ─────────────────────────────────────────────
#  Add Channel
# ─────────────────────────────────────────────

def _build_add_channel_dialog(event, holder):
    dlg = _Dialog("Add Channel", "Monitor a YouTube channel for live streams", ACCENT, event, holder)
    body = dlg.body

    _label(body, "Channel handle or URL", F_SMALL, TEXT_DIM).pack(anchor="w")
    field = tk.Frame(body, bg=SURFACE2, highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=ACCENT)
    field.pack(fill="x", pady=(6, 0))
    _label(field, "@", F_MONO, TEXT_DIM, SURFACE2, padx=(10)).pack(side="left")
    var = tk.StringVar()
    entry = tk.Entry(field, textvariable=var, bg=SURFACE2, fg=TEXT, insertbackground=TEXT,
                     relief="flat", bd=0, font=F_MONO, highlightthickness=0, width=34)
    entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 10))
    entry.focus_set()

    hint = _label(body, "Paste a handle or a youtube.com/@channel link", F_SMALL, TEXT_FAINT, anchor="w")
    hint.pack(fill="x", pady=(8, 0))

    add_btn = dlg.button("Add Channel", lambda: submit())
    dlg.button("Cancel", lambda: dlg.finish(None), "ghost")
    add_btn.set_enabled(False)

    def on_change(*_):
        text = var.get()
        if text.startswith("@"):          # the visible "@" is already in the field
            var.set(text.lstrip("@"))
            return
        handle = _parse_handle(text)
        if handle:
            hint.config(text=f"✓  Will monitor {handle}", fg=SUCCESS)
        else:
            hint.config(text="Paste a handle or a youtube.com/@channel link" if not text
                        else "That doesn't look like a valid handle", fg=TEXT_FAINT if not text else DANGER)
        add_btn.set_enabled(handle is not None)

    def submit():
        handle = _parse_handle(var.get())
        if handle:
            dlg.finish(handle)

    var.trace_add("write", on_change)
    entry.bind("<Return>", lambda _: submit())
    dlg.show()


# ─────────────────────────────────────────────
#  Remove / View channels
# ─────────────────────────────────────────────

def _build_channel_manager_dialog(channels: dict, default_channels: dict, mode: str, event, holder):
    removing = mode == "remove"
    dlg = _Dialog(
        "Remove Channel" if removing else "Monitored Channels",
        "Select a channel to stop monitoring" if removing else f"{len(channels)} channel(s) active",
        ACCENT if removing else INFO, event, holder)
    body = dlg.body

    handles = list(channels)
    selected: list[str | None] = [None]
    rows: dict[str, dict] = {}

    query = tk.StringVar()
    if len(handles) > 6:
        box = tk.Frame(body, bg=SURFACE2, highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=INFO)
        box.pack(fill="x", pady=(0, 10))
        _label(box, "🔍", F_SMALL, TEXT_DIM, SURFACE2, padx=8).pack(side="left")
        tk.Entry(box, textvariable=query, bg=SURFACE2, fg=TEXT, insertbackground=TEXT, relief="flat",
                 bd=0, font=F_BODY, highlightthickness=0).pack(side="left", fill="x", expand=True, ipady=6)

    # Scrollable list
    height = min(max(len(handles), 1), 7) * 52
    wrap = tk.Frame(body, bg=SURFACE)
    wrap.pack(fill="both", expand=True)
    cv = tk.Canvas(wrap, bg=SURFACE, highlightthickness=0, bd=0, width=380, height=height)
    inner = tk.Frame(cv, bg=SURFACE)
    cv.create_window((0, 0), window=inner, anchor="nw", width=380)
    cv.pack(side="left", fill="both", expand=True)
    inner.bind("<Configure>", lambda _: cv.configure(scrollregion=cv.bbox("all")))

    def wheel(e):
        if inner.winfo_reqheight() > height:
            cv.yview_scroll(-1 * (e.delta // 120), "units")
    dlg.win.bind("<MouseWheel>", wheel)

    remove_btn = None

    def paint(handle):
        r = rows[handle]
        chosen = selected[0] == handle
        colour = SURFACE2 if (chosen or r["hover"]) else SURFACE
        for w in r["widgets"]:
            w.config(bg=colour)
        r["bar"].config(bg=ACCENT if chosen else colour)

    def select(handle):
        if not removing:
            return
        prev, selected[0] = selected[0], handle
        if prev in rows:
            paint(prev)
        paint(handle)
        remove_btn.set_enabled(True)

    def build_rows(*_):
        for child in inner.winfo_children():
            child.destroy()
        rows.clear()
        q = query.get().strip().lower()
        for handle in handles:
            name = channels[handle]
            if q and q not in name.lower() and q not in handle.lower():
                continue
            row = tk.Frame(inner, bg=SURFACE, height=48, cursor="hand2" if removing else "arrow")
            row.pack(fill="x", pady=2)
            row.pack_propagate(False)
            bar = tk.Frame(row, bg=SURFACE, width=3)
            bar.pack(side="left", fill="y")
            avatar = _avatar(row, name, SURFACE)
            avatar.pack(side="left", padx=(10, 12))
            texts = tk.Frame(row, bg=SURFACE)
            texts.pack(side="left", fill="x", expand=True)
            n_lbl = _label(texts, _ellipsize(name, 34), F_BOLD, anchor="w")
            n_lbl.pack(fill="x")
            h_lbl = _label(texts, handle, F_TINY, TEXT_DIM, anchor="w")
            h_lbl.pack(fill="x")
            widgets = [row, avatar, texts, n_lbl, h_lbl]
            if handle in default_channels:
                tag = _label(row, "BUILT-IN", F_TINY, TEXT_DIM, padx=10)
                tag.pack(side="right")
                widgets.append(tag)
            rows[handle] = {"widgets": widgets, "bar": bar, "hover": False}
            for w in widgets + [bar]:
                w.bind("<Button-1>", lambda _, h=handle: select(h))
                w.bind("<Enter>", lambda _, h=handle: (rows[h].update(hover=True), paint(h)))
                w.bind("<Leave>", lambda _, h=handle: (rows[h].update(hover=False), paint(h)))
                w.bind("<MouseWheel>", wheel)
            if selected[0] == handle:
                paint(handle)
        if not rows:
            _label(inner, "No channels match", F_BODY, TEXT_DIM, pady=20).pack()

    build_rows()
    query.trace_add("write", build_rows)

    if removing:
        remove_btn = dlg.button("Remove", lambda: dlg.finish(selected[0]), "danger")
        remove_btn.set_enabled(False)
        dlg.button("Cancel", lambda: dlg.finish(None), "ghost")
    else:
        dlg.button("Close", lambda: dlg.finish(None), "ghost")
    dlg.show()


# ─────────────────────────────────────────────
#  Live toast (stacked, animated)
# ─────────────────────────────────────────────

class _Toast:
    def __init__(self, channel_name, title, url, viewers, start_time, thumb):
        self.url = url
        self.life = TOAST_LIFE
        self.hover = False
        self.closing = False
        self.x_off = float(TOAST_W + TOAST_PAD)   # slides in from the right
        self.y = None                              # set on first layout
        self.target_y = 0.0
        self.alpha = 0.0

        root = _UI_ROOT
        self.win = win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.attributes("-transparentcolor", KEY)
        win.configure(bg=KEY)
        _dwm(win, 33, 1)   # no DWM rounding: the card draws its own corners

        self.cv = cv = tk.Canvas(win, width=TOAST_W, height=TOAST_H, bg=KEY, highlightthickness=0, bd=0)
        cv.pack()
        self._photo = ImageTk.PhotoImage(_card_image(thumb))   # keep a reference
        cv.create_image(0, 0, anchor="nw", image=self._photo)

        # LIVE pill over the thumbnail
        _rrect(cv, 20, 14 + THUMB_H - 24, 66, 14 + THUMB_H - 8, 6, fill=ACCENT)
        cv.create_text(43, 14 + THUMB_H - 16, text="● LIVE", fill="#fff", font=(FAMILY, 7, "bold"))

        tx = 14 + THUMB_W + 14
        tw = TOAST_W - tx - 16
        cv.create_text(tx, 16, text=_ellipsize(channel_name, 30).upper(), fill=TEXT_DIM,
                       font=(FAMILY, 8, "bold"), anchor="nw")
        cv.create_text(tx, 34, text=_ellipsize(title, 54), fill=TEXT, font=F_BOLD, anchor="nw", width=tw)
        meta = "  ·  ".join(filter(None, [_format_viewers(viewers), _format_elapsed(start_time)]))
        if meta:
            cv.create_text(tx, 14 + THUMB_H - 14, text=meta, fill=TEXT_DIM, font=F_TINY, anchor="nw")

        # Buttons
        self.watch = _Button(cv, "▶  Watch now", self._watch, height=30, font=F_SMALL, bg=SURFACE)
        cv.create_window(14, TOAST_H - 18, anchor="sw", window=self.watch)
        self.dismiss = _Button(cv, "Dismiss", self.close, "ghost", height=30, font=F_SMALL, bg=SURFACE)
        cv.create_window(14 + self.watch._bw + 8, TOAST_H - 18, anchor="sw", window=self.dismiss)

        # Countdown bar
        self.bar = cv.create_rectangle(14, TOAST_H - 8, TOAST_W - 14, TOAST_H - 6, fill=ACCENT, outline="")

        win.bind("<Enter>", lambda _: setattr(self, "hover", True))
        win.bind("<Leave>", lambda _: setattr(self, "hover", False))

    def _watch(self):
        webbrowser.open(self.url)
        self.close()

    def close(self):
        self.closing = True

    def step(self) -> bool:
        """Advance one animation frame. Returns False once the window is gone."""
        if self.closing:
            self.alpha -= 0.1
            if self.alpha <= 0:
                self.win.destroy()
                return False
        else:
            self.x_off += (0 - self.x_off) * 0.2
            self.alpha = min(1.0, self.alpha + 0.12)
            if not self.hover and self.x_off < 2:
                self.life -= TICK
                frac = max(0.0, self.life / TOAST_LIFE)
                self.cv.coords(self.bar, 14, TOAST_H - 8, 14 + (TOAST_W - 28) * frac, TOAST_H - 6)
                if self.life <= 0:
                    self.closing = True
        self.y += (self.target_y - self.y) * 0.25
        return True

    def place(self, x_final: int):
        self.win.geometry(f"{TOAST_W}x{TOAST_H}+{int(x_final + self.x_off)}+{int(self.y)}")
        self.win.attributes("-alpha", max(0.0, min(1.0, self.alpha)))


_toasts: list[_Toast] = []
_ticking = False


def _layout():
    l, t, r, b = _work_area(_UI_ROOT)
    y = b - TOAST_PAD
    for toast in reversed(_toasts):            # newest at the bottom
        y -= TOAST_H
        toast.target_y = y
        if toast.y is None:
            toast.y = y + TOAST_H // 2
        y -= TOAST_PAD


def _tick():
    global _ticking
    _, _, right, _ = _work_area(_UI_ROOT)
    x_final = right - TOAST_W - TOAST_PAD
    alive = [t for t in _toasts if t.step()]
    changed = len(alive) != len(_toasts)
    _toasts[:] = alive
    if changed:
        _layout()
    for toast in _toasts:
        toast.place(x_final)
    if _toasts:
        _UI_ROOT.after(TICK, _tick)
    else:
        _ticking = False


def _build_live_toast(channel_name, title, url, viewers, start_time, thumb):
    global _ticking
    toast = _Toast(channel_name, title, url, viewers, start_time, thumb)
    _toasts.append(toast)
    for old in _toasts[:-TOAST_MAX]:
        old.close()
    _layout()
    if not _ticking:
        _ticking = True
        _UI_ROOT.after(TICK, _tick)


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────

def _run_dialog(builder, *args):
    """Build a dialog on the UI thread and block until it closes; return its result."""
    event, result = threading.Event(), [None]
    _post(lambda: builder(*args, event, result))
    event.wait()
    return result[0]


def open_add_channel_dialog() -> str | None:
    """Blocking — returns the entered handle (e.g. '@OuroKronii'), or None if cancelled."""
    return _run_dialog(_build_add_channel_dialog)


def open_remove_channel_dialog(channels: dict, default_channels: dict) -> str | None:
    """Blocking — returns the handle selected for removal, or None if cancelled."""
    return _run_dialog(_build_channel_manager_dialog, channels, default_channels, "remove")


def open_view_channels_dialog(channels: dict, default_channels: dict):
    """Blocking — read-only list of monitored channels."""
    _run_dialog(_build_channel_manager_dialog, channels, default_channels, "view")


def show_live_toast(channel_name: str, stream_title: str, stream_url: str,
                    thumbnail_url: str | None = None,
                    viewers: str | None = None,
                    start_time: str | None = None):
    """Non-blocking. Fetches the thumbnail on a worker thread, then builds the toast on the UI thread."""
    threading.Thread(target=_play_sound, daemon=True).start()

    def _fetch_then_build():
        thumb = _fetch_thumbnail(thumbnail_url) if thumbnail_url else None
        _post(lambda: _build_live_toast(channel_name, stream_title, stream_url,
                                        viewers, start_time, thumb))

    threading.Thread(target=_fetch_then_build, daemon=True).start()

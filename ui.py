"""
ui.py — Custom UI components for YouTube Live Notifier

Threading model
───────────────
Tkinter's Tcl interpreter is NOT thread-safe.  Every widget creation,
configuration, and destruction must happen on exactly one thread — the
UI thread started by _ensure_ui_thread().

Rule: nothing outside this file ever touches Tkinter directly.
      All callers use the public functions at the bottom, which either:
        • post a job onto _UI_QUEUE  (fire-and-forget, non-blocking), or
        • post a job and block on a threading.Event for the result.

The UI thread runs a hidden Tk root that never closes.  Toasts and
dialogs are Toplevel windows parented to that root.  The thumbnail
fetch is the only work that still runs on a worker thread, but it
posts its result back via root.after() which is safe because
root.after() is one of the few Tkinter calls that IS thread-safe.
"""

import io
import queue
import threading
import tkinter as tk
import webbrowser
import requests
from PIL import Image, ImageTk, ImageDraw, ImageOps


# ─────────────────────────────────────────────
#  Palette & constants
# ─────────────────────────────────────────────
BG         = "#0f0f0f"
BG2        = "#1a1a1a"
BG3        = "#242424"
BORDER     = "#2e2e2e"
ACCENT     = "#ff0000"
TEXT       = "#f0f0f0"
TEXT_DIM   = "#888888"
SUCCESS    = "#22c55e"
DANGER     = "#ef4444"

FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_BODY  = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO  = ("Consolas", 10)

TOAST_W    = 400
TOAST_H    = 150
TOAST_PAD  = 16
TOAST_LIFE = 6000   # ms before auto-dismiss
TOAST_ANIM = 18     # ms per animation frame


# ─────────────────────────────────────────────
#  Single UI thread
# ─────────────────────────────────────────────
_UI_QUEUE:  queue.Queue   = queue.Queue()
_UI_ROOT:   tk.Tk | None  = None
_UI_THREAD: threading.Thread | None = None

# Keeps strong references to active toast Toplevel windows so Python's GC
# doesn't destroy them (and their canvas/PhotoImage contents) before the
# thumbnail worker thread has finished placing the image.
_active_toasts: set = set()


def _set_dpi_aware():
    """Tell Windows this process handles its own DPI scaling.
    Must be called before any Tk window is created."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()   # legacy fallback
        except Exception:
            pass


def _ui_thread_main():
    """Entry point for the single persistent UI thread."""
    global _UI_ROOT

    _set_dpi_aware()
    root = tk.Tk()
    root.withdraw()          # hidden forever — only Toplevels are visible
    root.title("__notifier_root__")
    _apply_dark_title_bar(root)
    _UI_ROOT = root

    # Poll the job queue every 50 ms and run any pending callables
    def _poll():
        try:
            while True:
                fn = _UI_QUEUE.get_nowait()
                try:
                    fn()
                except Exception as e:
                    print(f"[UI] job error: {e}")
                    import traceback; traceback.print_exc()
        except queue.Empty:
            pass
        root.after(50, _poll)

    root.after(50, _poll)
    root.mainloop()


def _ensure_ui_thread():
    """Start the UI thread exactly once."""
    global _UI_THREAD
    if _UI_THREAD is None or not _UI_THREAD.is_alive():
        _UI_THREAD = threading.Thread(target=_ui_thread_main, daemon=True, name="UIThread")
        _UI_THREAD.start()
        # Give Tk a moment to initialise before callers try to use it
        import time
        time.sleep(0.15)


def _post(fn):
    """Schedule fn() to run on the UI thread (non-blocking)."""
    _ensure_ui_thread()
    _UI_QUEUE.put(fn)


def _call(fn):
    """
    Schedule fn() on the UI thread and block until it returns.
    fn must return its result; that value is passed back to the caller.
    """
    _ensure_ui_thread()
    result_holder = [None]
    done = threading.Event()

    def _wrapper():
        try:
            result_holder[0] = fn()
        except Exception as e:
            print(f"[UI] _call error: {e}")
        finally:
            done.set()

    _UI_QUEUE.put(_wrapper)
    done.wait()
    return result_holder[0]


# ─────────────────────────────────────────────
#  Helpers (pure / PIL — safe on any thread)
# ─────────────────────────────────────────────

def _lighten(hex_color: str, amount: int = 25) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(
        min(255, r + amount), min(255, g + amount), min(255, b + amount)
    )


def _apply_dark_title_bar(win):
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass


def _rounded_photo(img: Image.Image, radius: int = 8) -> ImageTk.PhotoImage:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *img.size), radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, mask=mask)
    bg = Image.new("RGBA", img.size, BG2 + "ff")
    bg.paste(out, mask=out.split()[3])
    # ImageTk.PhotoImage must be created on the UI thread — return raw PIL here
    return bg.convert("RGB")


def _fetch_thumbnail_pil(url: str, size: tuple) -> Image.Image | None:
    """
    Fetch and resize thumbnail — returns a PIL Image (thread-safe).
    YouTube's maxresdefault.jpg doesn't always exist; falls back to hqdefault.
    """
    urls_to_try = [url]
    if "maxresdefault" in url:
        urls_to_try.append(url.replace("maxresdefault", "hqdefault"))

    for attempt_url in urls_to_try:
        try:
            r = requests.get(attempt_url, timeout=8)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                img = ImageOps.fit(img, size, Image.LANCZOS)
                return _rounded_photo(img, radius=6)
        except Exception as e:
            print(f"Thumbnail fetch error ({attempt_url}): {e}")
    return None


def _play_sound():
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


def _format_viewers(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return f"{int(raw):,} watching"
    except ValueError:
        return None


def _format_elapsed(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            fixed = iso[:-3] + iso[-2:]
            dt = datetime.strptime(fixed, "%Y-%m-%dT%H:%M:%S%z")
        elapsed = int((datetime.now(timezone.utc) - dt).total_seconds())
        if elapsed < 60:
            return "Just started"
        elif elapsed < 3600:
            return f"Started {elapsed // 60}m ago"
        else:
            h, m = elapsed // 3600, (elapsed % 3600) // 60
            return f"Started {h}h {m}m ago" if m else f"Started {h}h ago"
    except Exception as e:
        print("Elapsed time error:", e)
        return None


# ─────────────────────────────────────────────
#  Widget factories (must be called on UI thread)
# ─────────────────────────────────────────────

def _btn(parent, text, command, style="primary", **kw) -> tk.Button:
    colors = {"primary": (ACCENT, "#fff"), "ghost": (BG3, TEXT_DIM), "danger": (DANGER, "#fff")}
    bg, fg = colors.get(style, colors["primary"])
    b = tk.Button(parent, text=text, command=command,
                  bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                  relief="flat", bd=0, cursor="hand2",
                  font=FONT_BODY, padx=14, pady=7, **kw)
    b.bind("<Enter>", lambda _: b.config(bg=_lighten(bg)))
    b.bind("<Leave>", lambda _: b.config(bg=bg))
    return b


def _label(parent, text, style="body", **kw) -> tk.Label:
    styles = {
        "title":  {"font": FONT_TITLE, "fg": TEXT},
        "body":   {"font": FONT_BODY,  "fg": TEXT},
        "dim":    {"font": FONT_SMALL, "fg": TEXT_DIM},
        "accent": {"font": FONT_BODY,  "fg": ACCENT},
    }
    cfg = {**styles.get(style, styles["body"]), "bg": kw.pop("bg", BG2)}
    return tk.Label(parent, text=text, **cfg, **kw)


def _centre(win):
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    x = (win.winfo_screenwidth()  - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"+{x}+{y}")


# ─────────────────────────────────────────────
#  Add Channel Dialog
# ─────────────────────────────────────────────

def _build_add_channel_dialog(result_event: threading.Event, result_holder: list):
    """Must be called on the UI thread."""
    root = _UI_ROOT

    win = tk.Toplevel(root)
    win.title("Add Channel")
    win.configure(bg=BG2)
    win.resizable(False, False)
    _apply_dark_title_bar(win)

    def _finish(value):
        result_holder[0] = value
        result_event.set()
        win.destroy()

    # Header
    header = tk.Frame(win, bg=BG, pady=14)
    header.pack(fill="x")
    tk.Frame(header, bg=ACCENT, width=4).pack(side="left", fill="y", padx=(0, 12))
    hbox = tk.Frame(header, bg=BG)
    hbox.pack(side="left")
    _label(hbox, "Add Channel",  style="title", bg=BG).pack(anchor="w")
    _label(hbox, "Monitor a new YouTube channel for live streams", style="dim", bg=BG).pack(anchor="w", pady=(2, 0))

    # Body
    body = tk.Frame(win, bg=BG2, padx=20, pady=16)
    body.pack(fill="both")
    _label(body, "YouTube Handle", style="dim").pack(anchor="w")

    ef = tk.Frame(body, bg=BG3, highlightthickness=1,
                  highlightbackground=BORDER, highlightcolor=ACCENT)
    ef.pack(fill="x", pady=(4, 0))
    tk.Label(ef, text="@", bg=BG3, fg=TEXT_DIM, font=FONT_MONO, padx=8, pady=8).pack(side="left")
    entry = tk.Entry(ef, bg=BG3, fg=TEXT, insertbackground=TEXT,
                     relief="flat", bd=0, font=FONT_MONO, highlightthickness=0)
    entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
    entry.focus_set()

    _label(body, "e.g.  OuroKronii  or  @OuroKronii", style="dim").pack(anchor="w", pady=(4, 0))

    status_var = tk.StringVar()
    status_lbl = tk.Label(body, textvariable=status_var, bg=BG2, fg=SUCCESS,
                          font=FONT_SMALL, anchor="w")
    status_lbl.pack(fill="x", pady=(6, 0))

    # Footer
    footer = tk.Frame(win, bg=BG, padx=20, pady=12)
    footer.pack(fill="x")

    def _submit():
        raw = entry.get().strip().lstrip("@")
        if not raw:
            status_var.set("⚠  Please enter a handle.")
            status_lbl.config(fg=DANGER)
            return
        _finish("@" + raw)

    _btn(footer, "Cancel", lambda: _finish(None), style="ghost").pack(side="right", padx=(6, 0))
    add_btn = _btn(footer, "Add Channel", _submit)
    add_btn.pack(side="right")

    entry.bind("<Return>", lambda _: _submit())
    win.bind("<Escape>", lambda _: _finish(None))
    win.protocol("WM_DELETE_WINDOW", lambda: _finish(None))

    _centre(win)
    win.deiconify()
    win.grab_set()


# ─────────────────────────────────────────────
#  Remove / View Dialog
# ─────────────────────────────────────────────

def _build_channel_manager_dialog(channels: dict, default_channels: dict, mode: str,
                                   result_event: threading.Event, result_holder: list):
    root = _UI_ROOT

    title = "Remove Channel" if mode == "remove" else "Monitored Channels"
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=BG2)
    win.resizable(False, False)
    _apply_dark_title_bar(win)

    def _finish(value=None):
        result_holder[0] = value
        result_event.set()
        win.destroy()

    # Header
    bar_color = ACCENT if mode == "remove" else "#3b82f6"
    header = tk.Frame(win, bg=BG, pady=14)
    header.pack(fill="x")
    tk.Frame(header, bg=bar_color, width=4).pack(side="left", fill="y", padx=(0, 12))
    hbox = tk.Frame(header, bg=BG)
    hbox.pack(side="left")
    if mode == "remove":
        _label(hbox, "Remove Channel", style="title", bg=BG).pack(anchor="w")
        _label(hbox, "Select a channel to stop monitoring", style="dim", bg=BG).pack(anchor="w", pady=(2, 0))
    else:
        _label(hbox, "Monitored Channels", style="title", bg=BG).pack(anchor="w")
        _label(hbox, f"{len(channels)} channel(s) active", style="dim", bg=BG).pack(anchor="w", pady=(2, 0))

    # List
    body = tk.Frame(win, bg=BG2, padx=20, pady=12)
    body.pack(fill="both")
    lf = tk.Frame(body, bg=BORDER, bd=1)
    lf.pack(fill="both")
    sb = tk.Scrollbar(lf, bg=BG3, troughcolor=BG2, activebackground=TEXT_DIM,
                      relief="flat", bd=0)
    sb.pack(side="right", fill="y")
    listbox = tk.Listbox(lf, bg=BG3, fg=TEXT, selectbackground=ACCENT,
                         selectforeground="#fff", font=FONT_BODY,
                         relief="flat", bd=0, highlightthickness=0,
                         activestyle="none", width=46,
                         height=min(len(channels), 8) + 1,
                         yscrollcommand=sb.set)
    listbox.pack(side="left", fill="both", expand=True)
    sb.config(command=listbox.yview)

    handle_list = list(channels.keys())
    for h in handle_list:
        tag = "  ·  built-in" if h in default_channels else ""
        listbox.insert(tk.END, f"  {channels[h]}{tag}")

    # Footer
    footer = tk.Frame(win, bg=BG, padx=20, pady=12)
    footer.pack(fill="x")
    _btn(footer, "Close", lambda: _finish(None), style="ghost").pack(side="right", padx=(6, 0))

    if mode == "remove":
        def _remove():
            sel = listbox.curselection()
            if sel:
                _finish(handle_list[sel[0]])
        _btn(footer, "Remove Selected", _remove, style="danger").pack(side="right")

    win.bind("<Escape>", lambda _: _finish(None))
    win.protocol("WM_DELETE_WINDOW", lambda: _finish(None))
    _centre(win)
    win.deiconify()
    win.grab_set()


# ─────────────────────────────────────────────
#  Live Toast
# ─────────────────────────────────────────────

# Thumbnail dimensions — referenced by show_live_toast before _build_live_toast
TOAST_THUMB_W = 128
TOAST_THUMB_H = TOAST_H - 20


def _build_live_toast(channel_name: str, stream_title: str, stream_url: str,
                      viewers: str | None, start_time: str | None,
                      pil_img):
    """
    Build and animate the toast entirely on the UI thread.
    pil_img is a PIL Image (already fetched) or None.
    PhotoImage is created here, on the UI thread, with no async swap needed.
    """
    root = _UI_ROOT
    W, H = TOAST_W, TOAST_H
    THUMB_W, THUMB_H = TOAST_THUMB_W, TOAST_THUMB_H
    THUMB_X, THUMB_Y = 10, 10
    TX = THUMB_X + THUMB_W + 12
    TW = W - TX - 12

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x_end  = sw - W - TOAST_PAD
    y_end  = sh - H - TOAST_PAD - 48
    y_start = sh

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.0)
    win.configure(bg=BG2)
    win.geometry(f"{W}x{H}+{x_end}+{y_start}")

    canvas = tk.Canvas(win, width=W, height=H, bg=BG2,
                       highlightthickness=1, highlightbackground=BORDER, bd=0)
    canvas.pack(fill="both", expand=True)

    # ── Thumbnail ─────────────────────────────────────────────────────────
    # Convert PIL → PhotoImage here on the UI thread.
    # Store on win so it stays alive as long as the window does.
    if pil_img is not None:
        win._photo = ImageTk.PhotoImage(pil_img)
        canvas.create_image(THUMB_X, THUMB_Y, anchor="nw", image=win._photo)
    else:
        # No image — draw a dark placeholder
        canvas.create_rectangle(THUMB_X, THUMB_Y, THUMB_X + THUMB_W, THUMB_Y + THUMB_H,
                                 fill=BG3, outline="")
        canvas.create_text(THUMB_X + THUMB_W // 2, THUMB_Y + THUMB_H // 2,
                           text="No preview", fill=TEXT_DIM, font=("Segoe UI", 7))

    # ── Overlay (always drawn after image so it sits on top) ──────────────
    bw = 44
    canvas.create_rectangle(0, 0, W, 4, fill=ACCENT, outline="")
    canvas.create_rectangle(TX, 12, TX + bw, 28, fill=ACCENT, outline="")
    canvas.create_text(TX + bw // 2, 20, text="● LIVE", fill="white",
                       font=("Segoe UI", 7, "bold"), anchor="center")
    canvas.create_text(TX + bw + 6, 20, text=channel_name,
                       fill=TEXT, font=("Segoe UI", 9, "bold"), anchor="w")

    t = stream_title if len(stream_title) <= 60 else stream_title[:57] + "…"
    canvas.create_text(TX, 34, text=t, fill=TEXT,
                       font=("Segoe UI", 9, "bold"), anchor="nw", width=TW)

    viewers_str = _format_viewers(viewers)
    if viewers_str:
        canvas.create_text(TX, 62, text=f"👁  {viewers_str}",
                           fill=TEXT_DIM, font=("Segoe UI", 8), anchor="nw")

    elapsed_str = _format_elapsed(start_time)
    if elapsed_str:
        canvas.create_text(TX, 78, text=f"🕐  {elapsed_str}",
                           fill=TEXT_DIM, font=("Segoe UI", 8), anchor="nw")

    bx, by, bw2, bh = TX, H - 26, 96, 22
    watch_rect = canvas.create_rectangle(bx, by, bx + bw2, by + bh,
                                          fill=ACCENT, outline="", tags="watch")
    canvas.create_text(bx + bw2 // 2, by + bh // 2, text="▶  Watch Now",
                       fill="white", font=("Segoe UI", 8, "bold"), tags="watch")
    canvas.create_text(W - 14, 14, text="✕", fill=TEXT_DIM,
                       font=("Segoe UI", 10), tags="dismiss", anchor="center")

    # ── Interactions ──────────────────────────────────────────────────────
    _destroyed = [False]
    _active_toasts.add(win)

    def _safe_destroy():
        if not _destroyed[0]:
            _destroyed[0] = True
            _active_toasts.discard(win)
            win.destroy()

    canvas.tag_bind("watch",   "<Button-1>", lambda _: [webbrowser.open(stream_url), _safe_destroy()])
    canvas.tag_bind("dismiss", "<Button-1>", lambda _: _safe_destroy())
    canvas.tag_bind("watch",   "<Enter>", lambda _: canvas.itemconfig(watch_rect, fill=_lighten(ACCENT)))
    canvas.tag_bind("watch",   "<Leave>", lambda _: canvas.itemconfig(watch_rect, fill=ACCENT))
    canvas.tag_bind("dismiss", "<Enter>", lambda _: canvas.itemconfig("dismiss", fill=TEXT))
    canvas.tag_bind("dismiss", "<Leave>", lambda _: canvas.itemconfig("dismiss", fill=TEXT_DIM))

    _dx = [0]; _dy = [0]
    def _drag_start(e): _dx[0], _dy[0] = e.x_root, e.y_root
    def _drag_move(e):
        win.geometry(f"+{win.winfo_x()+e.x_root-_dx[0]}+{win.winfo_y()+e.y_root-_dy[0]}")
        _dx[0], _dy[0] = e.x_root, e.y_root
    canvas.bind("<ButtonPress-1>", _drag_start)
    canvas.bind("<B1-Motion>",     _drag_move)

    # ── Animations ────────────────────────────────────────────────────────
    def _slide_in(cur_y):
        if _destroyed[0]:
            return
        if cur_y <= y_end:
            win.attributes("-alpha", 1.0)
            win.after(TOAST_LIFE, _fade_out)
            return
        new_y = cur_y - max(1, (cur_y - y_end) // 4)
        win.geometry(f"{W}x{H}+{x_end}+{new_y}")
        win.attributes("-alpha", min(1.0, 1.0 - (new_y - y_end) / H))
        win.after(TOAST_ANIM, lambda: _slide_in(new_y))

    def _fade_out(a=1.0):
        if _destroyed[0]:
            return
        if a <= 0:
            _safe_destroy()
            return
        win.attributes("-alpha", a)
        win.after(TOAST_ANIM, lambda: _fade_out(round(a - 0.07, 3)))

    win.after(50, lambda: _slide_in(y_start))


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────

def open_add_channel_dialog() -> str | None:
    """
    Blocking — shows the Add Channel dialog and returns the entered
    handle string (e.g. '@OuroKronii'), or None if cancelled.
    """
    event  = threading.Event()
    result = [None]
    _post(lambda: _build_add_channel_dialog(event, result))
    event.wait()
    return result[0]


def open_remove_channel_dialog(channels: dict, default_channels: dict) -> str | None:
    """
    Blocking — shows the Remove Channel dialog and returns the handle
    selected for removal, or None if cancelled.
    """
    event  = threading.Event()
    result = [None]
    _post(lambda: _build_channel_manager_dialog(channels, default_channels, "remove", event, result))
    event.wait()
    return result[0]


def open_view_channels_dialog(channels: dict, default_channels: dict):
    """Blocking — shows the View Channels dialog (read-only)."""
    event  = threading.Event()
    result = [None]
    _post(lambda: _build_channel_manager_dialog(channels, default_channels, "view", event, result))
    event.wait()


def show_live_toast(channel_name: str, stream_title: str, stream_url: str,
                    thumbnail_url: str | None = None,
                    viewers: str | None = None,
                    start_time: str | None = None):
    """
    Non-blocking.
    Fetches the thumbnail PIL image on a worker thread (network I/O),
    then posts the full toast build to the UI thread once the image is
    ready — so PhotoImage creation and all canvas work happen on the UI
    thread with the image already in hand. No async swap, no z-order issues.
    """
    threading.Thread(target=_play_sound, daemon=True).start()

    def _fetch_then_build():
        pil_img = None
        if thumbnail_url:
            pil_img = _fetch_thumbnail_pil(
                thumbnail_url, (TOAST_THUMB_W, TOAST_THUMB_H)
            )
        # Post the build to the UI thread — pil_img may be None if fetch failed
        _post(lambda: _build_live_toast(
            channel_name, stream_title, stream_url,
            viewers, start_time, pil_img
        ))

    threading.Thread(target=_fetch_then_build, daemon=True).start()
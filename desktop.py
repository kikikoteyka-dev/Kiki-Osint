#!/usr/bin/env python3
"""KikiHub desktop launcher.

Runs Flask in a background thread and opens a native window via pywebview
instead of requiring the user to open a terminal + browser tab manually.

While the backend warms up (importing app.py does a DNS lookup for the
Gemini API endpoint, which can take a few seconds on its own), this prints
a spinning ASCII donut (the classic donut.c algorithm by Andy Sloane) to
the console so the app doesn't look frozen. The import itself happens in
the SAME background thread as the Flask server, so the slow part never
blocks the animation from starting immediately.
"""
import ctypes
import math
import os
import sys
import threading
import time
import urllib.request

import webview

HOST, PORT = "127.0.0.1", 7777
BG_COLOR = "#04111a"  # matches the app's --bg CSS token, avoids a black flash

BANNER = r"""
   /\_/\
  ( o.o )   K I K I H U B
   > ^ <    starting up...
"""

_flask_app = {}


class Api:
    # Exposed to the frontend as window.pywebview.api.* — used so file saves
    # go through a native "Save As" dialog instead of a browser-style blob
    # download. WebView2's built-in download manager isn't configured by
    # pywebview here, and triggering it via an <a download> click on a blob
    # URL was crashing the whole window instead of just failing the save.
    def save_file(self, b64_data, suggested_name):
        import base64
        window = webview.windows[0]
        path = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=suggested_name)
        if not path:
            return {"ok": False, "error": "cancelled"}
        if isinstance(path, (list, tuple)):
            path = path[0]
        try:
            with open(path, "wb") as f:
                f.write(base64.b64decode(b64_data))
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_acrylic(self, level):
        # window.pywebview.api.set_acrylic('off'|'light'|'heavy') — retints
        # or disables the native blur-behind live, no restart needed.
        # 1=None, 2=Mica, 3=Acrylic, 4=Mica Alt (tabbed).
        #
        # Restored to the very first working config: light=Mica, heavy=
        # Acrylic. Mica samples the real desktop wallpaper's own colors
        # instead of using Acrylic's fixed cool blue-grey dark-mode recipe —
        # that's the actual source of "always looks blue no matter what CSS
        # you throw at it" (confirmed: our own overlay computes correctly,
        # the tint is baked into the native Acrylic material itself). Mica
        # was later swapped to Acrylic for both levels chasing an unrelated
        # bug (a white/black client area) — but the original combo, with
        # DwmExtendFrameIntoClientArea called plainly on every switch (see
        # _apply_acrylic), is what was screenshotted as genuinely working:
        # real desktop visible through live blur, no blue complaint at all.
        backdrop_type = _TRANSPARENT_LEVELS.get(level, 1)
        try:
            hwnd = _window_hwnd(webview.windows[0])
            _apply_acrylic(hwnd, backdrop_type)
            # Mirrors this into a plain file Python can read BEFORE creating
            # the window next launch — see _enable_acrylic_blur.
            _save_transparent_pref(level)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # window.pywebview.api.pick_file('hashcat'|'wordlist'|'tshark'|'pcap') —
    # a real native file picker for the tool-path settings (and for the WiFi
    # Cracker's "choose a pcap" flow, which used to shell out to a temp
    # tkinter script via `subprocess.run(["python", tmp])` — works on a dev
    # box with Python on PATH, but the whole point of a frozen exe is that
    # the target machine has no Python at all, so that silently did nothing
    # on a clean install. This goes through the same native dialog machinery
    # as save_file() above instead of assuming a scripting runtime exists.
    _FILE_DIALOG_FILTERS = {
        "hashcat": ("hashcat.exe (hashcat.exe)", "Executable (*.exe)", "All files (*.*)"),
        "wordlist": ("Wordlist (*.txt)", "All files (*.*)"),
        "tshark": ("tshark.exe (tshark.exe)", "Executable (*.exe)", "All files (*.*)"),
        "pcap": ("Capture files (*.pcap;*.pcapng;*.cap)", "All files (*.*)"),
    }

    def pick_file(self, kind):
        window = webview.windows[0]
        file_types = self._FILE_DIALOG_FILTERS.get(kind, ("All files (*.*)",))
        result = window.create_file_dialog(webview.FileDialog.OPEN, file_types=file_types)
        if not result:
            return {"ok": False, "error": "cancelled"}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return {"ok": True, "path": path}


def _run_flask():
    # Imported here (not at module scope) so the slow module-level work in
    # app.py — DNS probing for the Gemini API host — runs in this thread
    # instead of blocking the main thread before the loading screen appears.
    #
    # Flask/Werkzeug print their own startup banner + per-request access log
    # straight to the console — on the SAME console the main thread is
    # repainting with \x1b[H every frame for the donut animation. The two
    # writers race for the same screen, corrupting both (looked like "wrong
    # animation" + dropped frames). Silence both: werkzeug's access/info log
    # goes through the 'werkzeug' logger, but the " * Serving Flask app" /
    # " * Debug mode" lines go through click.echo directly (not logging),
    # so the logger alone doesn't catch them — patch show_server_banner too.
    import logging
    logging.getLogger("werkzeug").disabled = True
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None

    from app import app
    _flask_app["app"] = app
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)


def _enable_vt_mode():
    # Legacy Windows consoles don't interpret ANSI escape codes (clear
    # screen, cursor home) unless ENABLE_VIRTUAL_TERMINAL_PROCESSING is set
    # explicitly — without this the donut frames print one after another
    # instead of animating in place, which looks like nothing is happening.
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _create_desktop_shortcut():
    # Self-installing touch: a user who just downloads the lone .exe and
    # double-clicks it gets a normal desktop icon afterwards, instead of
    # having to manually create a shortcut to wherever they put the file.
    try:
        if not getattr(sys, "frozen", False):
            return  # only relevant for the packaged exe, not `python desktop.py`
        import os
        exe_path = sys.executable
        marker = os.path.join(os.path.dirname(exe_path), ".shortcut_created")
        if os.path.exists(marker):
            return
        desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "Рабочий стол")
        if not os.path.isdir(desktop):
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            return
        link_path = os.path.join(desktop, "KikiHub.lnk")
        ps_script = (
            "$s = New-Object -ComObject WScript.Shell;"
            f"$lnk = $s.CreateShortcut('{link_path}');"
            f"$lnk.TargetPath = '{exe_path}';"
            f"$lnk.WorkingDirectory = '{os.path.dirname(exe_path)}';"
            f"$lnk.IconLocation = '{exe_path},0';"
            "$lnk.Save()"
        )
        import subprocess
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, timeout=10,
        )
        with open(marker, "w") as f:
            f.write("1")
    except Exception:
        pass


def _hide_console():
    # ShowWindow(SW_HIDE) alone only hides the console — the process stays
    # ATTACHED to it. If the user finds that hidden console some other way
    # (Alt+Tab surfaces it on some setups, or Task Manager's "Console Window
    # Host" entry) and closes it, Windows sends a close signal that kills
    # every process attached to that console — including this one — before
    # any Python try/except can react. FreeConsole() severs the attachment
    # entirely: with nothing left attached, the console itself goes away
    # for good instead of sitting around hidden-but-killable.
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass
    # FreeConsole() leaves sys.stdin/stdout/stderr pointing at handles that
    # no longer resolve to anything real. Most code never notices — but
    # asyncio's Windows event loop setup (used by the in-process Maigret
    # search) probes standard I/O handles while initializing and throws
    # "[WinError 6] The handle is invalid" the moment it does. Pointing
    # stdio at the null device gives it real, working OS handles instead
    # of severed console ones.
    try:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
    except Exception:
        pass


def _write(text):
    # sys.stdout is None when frozen with --windowed (no console attached) —
    # writing to it would crash the launcher before the window ever opens.
    if sys.stdout is None:
        return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass


# ── ASCII donut (donut.c by Andy Sloane, ported to Python) ──────────────
_LUMINANCE = ".,-~:;=!*#$@"
_W, _H = 42, 14  # overwritten by _size_canvas_to_console() at startup
_XSCALE, _YSCALE = 13, 6  # projection scale — kept proportional to _W/_H so the
                          # donut doesn't stretch when the canvas size changes


def _size_canvas_to_console():
    # Fill the actual console window instead of a small fixed box — a
    # character cell is roughly twice as tall as it is wide, so the x/y
    # projection scale has to stay proportional to width/height or the
    # donut comes out squashed.
    global _W, _H, _XSCALE, _YSCALE
    try:
        import shutil
        size = shutil.get_terminal_size(fallback=(42, 14))
        _W = max(20, size.columns - 2)
        _H = max(8, size.lines - 4)
    except Exception:
        pass
    _XSCALE = 13 * _W / 42
    _YSCALE = 6 * _H / 14

# Color the donut in KikiHub's own palette (--bg #04111a -> --a #5cd6ff)
# instead of leaving it plain white-on-black, so it looks like part of the
# app rather than a generic terminal demo.
_BG_RGB = (4, 17, 26)
_ACCENT_RGB = (92, 214, 255)
_COLOR_RAMP = [
    "\x1b[38;2;{};{};{}m".format(
        int(_BG_RGB[0] + (_ACCENT_RGB[0] - _BG_RGB[0]) * i / (len(_LUMINANCE) - 1)),
        int(_BG_RGB[1] + (_ACCENT_RGB[1] - _BG_RGB[1]) * i / (len(_LUMINANCE) - 1)),
        int(_BG_RGB[2] + (_ACCENT_RGB[2] - _BG_RGB[2]) * i / (len(_LUMINANCE) - 1)),
    )
    for i in range(len(_LUMINANCE))
]
_RESET = "\x1b[0m"


def _donut_frame(a, b):
    cos_a, sin_a = math.cos(a), math.sin(a)
    cos_b, sin_b = math.cos(b), math.sin(b)
    out = [" "] * (_W * _H)
    lum_idx = [-1] * (_W * _H)
    zbuf = [0.0] * (_W * _H)

    theta = 0.0
    while theta < 6.28:
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        phi = 0.0
        while phi < 6.28:
            cos_p, sin_p = math.cos(phi), math.sin(phi)
            circle_x, circle_y = cos_t + 2, sin_t

            x = circle_x * (cos_b * cos_p + sin_a * sin_b * sin_p) - circle_y * cos_a * sin_b
            y = circle_x * (sin_b * cos_p - sin_a * cos_b * sin_p) + circle_y * cos_a * cos_b
            z = 5 + cos_a * circle_x * sin_p + circle_y * sin_a
            ooz = 1 / z

            xp = int(_W / 2 + _XSCALE * ooz * x)
            yp = int(_H / 2 - _YSCALE * ooz * y)

            lum = (
                cos_p * cos_t * sin_b
                - cos_a * cos_t * sin_p
                - sin_a * sin_t
                + cos_b * (cos_a * sin_t - cos_t * sin_a * sin_p)
            )
            if lum > 0 and 0 <= xp < _W and 0 <= yp < _H:
                idx = xp + yp * _W
                if ooz > zbuf[idx]:
                    zbuf[idx] = ooz
                    li = min(int(lum * 8), len(_LUMINANCE) - 1)
                    lum_idx[idx] = li
                    out[idx] = _LUMINANCE[li]
            phi += 0.07
        theta += 0.07

    rows = []
    for r in range(_H):
        chars = []
        cur = None
        for c in range(_W):
            idx = r * _W + c
            li = lum_idx[idx]
            color = _COLOR_RAMP[li] if li >= 0 else _RESET
            if color != cur:
                chars.append(color)
                cur = color
            chars.append(out[idx])
        chars.append(_RESET)
        rows.append("".join(chars))
    return "\n".join(rows)


# ── Startup synchronisation events ──────────────────────────────────────────
# _flask_ready  — set when Flask first answers HTTP
# _donut_done   — set after the donut has been shown for at least MIN_DISPLAY s
# _nav_started  — set just before window.load_url() so the loaded callback
#                 knows the blank warm-up page is no longer the subject
# _revealed     — set once show()+_hide_console() have run, so the loaded
#                 callback and the reveal watchdog below don't double-fire
_flask_ready = threading.Event()
_donut_done  = threading.Event()
_nav_started = threading.Event()
_revealed    = threading.Event()
_quit_requested = threading.Event()
_tray_icon = None

MIN_DISPLAY = 5.0   # seconds the donut must be visible


def _run_donut(timeout=45):
    # Runs entirely in a background thread — the main thread is free to call
    # webview.start() immediately, so WebView2 initialises in parallel.
    url = f"http://{HOST}:{PORT}/"
    start = time.time()
    deadline = start + timeout
    a = b = 0.0
    server_up = False
    _write("\x1b[2J")
    while time.time() < deadline:
        if not server_up:
            try:
                urllib.request.urlopen(url, timeout=1)
                server_up = True
                _flask_ready.set()
            except Exception:
                pass
        elapsed = time.time() - start
        if server_up and elapsed >= MIN_DISPLAY:
            break
        frame = _donut_frame(a, b)
        _write(f"\x1b[H{frame}\n  starting backend...                    \n")
        a += 0.22
        b += 0.11
        time.sleep(0.03)
    _write("\x1b[H  ready.                                          \n")
    if not server_up:
        _flask_ready.set()   # unblock navigator even on timeout
    _donut_done.set()


def _navigate_when_ready(window):
    # Background thread: wait for Flask, then point the (already-warm) window at it.
    _flask_ready.wait(timeout=50)
    flask_up = True
    try:
        urllib.request.urlopen(f"http://{HOST}:{PORT}/", timeout=2)
    except Exception:
        flask_up = False
    _nav_started.set()
    if flask_up:
        try:
            window.load_url(f"http://{HOST}:{PORT}/")
        except Exception:
            pass
    else:
        # Flask never came up — show a retrying page.
        _donut_done.wait(timeout=60)
        _hide_console()
        try:
            window.load_html(
                "<body style='background:#04111a;color:#eef2f7;font-family:monospace;"
                "display:flex;align-items:center;justify-content:center;height:100vh;"
                "text-align:center;padding:20px'><div>KikiHub backend is taking longer "
                "than usual to start (slow network?).<br>Retrying…"
                f"<script>setTimeout(function(){{window.location.href='http://{HOST}:{PORT}/'}}"
                ",5000)</script></div></body>"
            )
            window.show()
        except Exception:
            pass


def _reveal(window):
    # Idempotent: the loaded callback and the watchdog below can both race to
    # call this, only the first one should actually show the window.
    if _revealed.is_set():
        return
    _revealed.set()
    try:
        # pywebview's own WinForms backend (platforms/winforms.py, create_window)
        # has an undocumented show->hide dance it runs itself for
        # transparent+Chromium windows ("hack to make transparent window
        # work... no idea why this works") — but ONLY when the window isn't
        # created with hidden=True, which we do (so the console/donut stays
        # up until Flask is ready). That means our window skips the one
        # thing that actually makes WebView2's transparent compositing
        # engage — DwmEnableBlurBehindWindow was genuinely blurring the real
        # desktop the whole time (confirmed on a bare test window), but
        # WebView2 itself was still painting an opaque-ish surface on top of
        # it because this initialization never ran. Replicating it here,
        # right before the real reveal.
        window.show()
        window.hide()
        window.show()
        # The hide()/show() cycle above resets DwmEnableBlurBehindWindow —
        # confirmed live: after adding the dance, the window went from
        # blue-tinted to solid BLACK instead of turning genuinely
        # transparent, meaning the blur-behind attribute set once at
        # _enable_acrylic_blur (creation time) no longer survives past this
        # point. Re-apply it now, after the dance, so it's active at the
        # moment the window is actually left visible.
        try:
            hwnd = _window_hwnd(window)
            if hwnd:
                level = _read_transparent_pref()
                _apply_acrylic(hwnd, _TRANSPARENT_LEVELS.get(level, 1))
                _strip_layered_style(hwnd)
                window.show()
                _guard_backdrop_theme(hwnd)
        except Exception as e:
            _write(f"\n  re-apply blur after reveal failed: {e!r}\n")
        # The hide()/show() dance above doesn't just reset DWM blur-behind —
        # it also tears down WebView2's own compositing layers, so any CSS
        # backdrop-filter (Card Backdrop glass) that painted correctly during
        # the hidden warm-up load is gone again by the time the window is
        # actually left on screen. Re-trigger it from here, now that the
        # dance is over, the same way _applyLiquidGlass already does when the
        # user flips the setting by hand (which is why toggling it off/on
        # after launch always "fixes" it — this just does that automatically).
        try:
            window.evaluate_js(
                "if(typeof _applyLiquidGlass==='function'){"
                "var cg=localStorage.getItem('kiki_card_glass');"
                "cg=cg===null?true:cg==='1';"
                "_applyLiquidGlass(cg);}"
            )
        except Exception as e:
            _write(f"\n  re-apply card glass after reveal failed: {e!r}\n")
        time.sleep(0.05)   # let the window actually paint before console vanishes
        _hide_console()
        _start_tray(window)
    except Exception as e:
        # Surface it instead of swallowing — a hidden window with a visible
        # console is worse than a console with a traceback in it.
        _write(f"\n  window reveal failed: {e!r}\n  KikiHub is still running — check Task Manager.\n")


def _on_loaded(window):
    # Spawned in a thread so we don't block the WebView2 GUI thread.
    if not _nav_started.is_set():
        # The blank warm-up page just finished loading — ignore it.
        return
    # Wait for the donut to finish its minimum display time, then reveal.
    _donut_done.wait(timeout=30)
    _reveal(window)


def _reveal_watchdog(window):
    # WebView2's second `loaded` event (after load_url navigates the warmed-up
    # window) doesn't always fire — observed as the console staying open
    # forever with the window never appearing, even though Flask is up and
    # the process is otherwise healthy. Belt-and-suspenders: force the reveal
    # a few seconds after navigation was attempted if _on_loaded never got
    # there on its own.
    _nav_started.wait(timeout=55)
    _donut_done.wait(timeout=35)
    if _revealed.wait(timeout=5):
        return
    _reveal(window)


def _tray_icon_image():
    from PIL import Image
    import os
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        return Image.open(os.path.join(base, "kiki_logo.png"))
    except Exception:
        return Image.new("RGB", (32, 32), BG_COLOR)


def _start_tray(window):
    # Called once the window is first shown — stands in for the console
    # (already hidden) so there's still a visible, clickable presence
    # instead of the app appearing to vanish after the donut screen.
    global _tray_icon
    if _tray_icon is not None:
        return
    import pystray

    def _open(icon=None, item=None):
        window.show()

    def _quit(icon=None, item=None):
        _quit_requested.set()
        icon.stop()
        try:
            window.destroy()
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem("Открыть KikiHub", _open, default=True),
        pystray.MenuItem("Выход", _quit),
    )
    _tray_icon = pystray.Icon("KikiHub", _tray_icon_image(), "KikiHub", menu)
    threading.Thread(target=_tray_icon.run, daemon=True).start()


def _on_closing(window):
    # The X button hides to tray instead of killing the process — without
    # this, closing the window was the only way to reach the tray icon,
    # which defeats the point of having one. Actual quit only happens via
    # the tray menu's "Выход", which sets _quit_requested first.
    if _quit_requested.is_set():
        return True
    window.hide()
    return False


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _window_hwnd(window):
    hwnd = getattr(window, "native", None)
    return hwnd.Handle.ToInt64() if hwnd is not None else None


class _DWM_BLURBEHIND(ctypes.Structure):
    _fields_ = [
        ("dwFlags", ctypes.c_uint32),
        ("fEnable", ctypes.c_int),
        ("hRgnBlur", ctypes.c_void_p),
        ("fTransitionOnMaximized", ctypes.c_int),
    ]


def _apply_acrylic(hwnd, backdrop_type):
    # DWMWA_SYSTEMBACKDROP_TYPE (Mica/Acrylic, the modern Win11 "material"
    # API) was replaced with the older DwmEnableBlurBehindWindow (the
    # Vista/7-era "Aero Glass" API, still functional on Win11) — confirmed
    # LIVE, side-by-side, that it's a genuinely different code path: Mica
    # and Acrylic both have a *fixed* color recipe baked in by Microsoft
    # (their own dark-mode palette is a cool blue-grey — that's not
    # adjustable through any public API, confirmed by testing every
    # DWMWA_SYSTEMBACKDROP_TYPE value and by prior research into
    # HostBackdropBrush/WinRT Composition, which has no public API for
    # this either). Blur-behind has no such material — it blurs whatever
    # is actually behind the window, live, with no tint of its own. Tested
    # with a bare test window before touching this file: real desktop
    # visible through live blur, no blue at all.
    #
    # backdrop_type here just means on/off (1=off, anything else=on) — this
    # API has no Mica-vs-Acrylic variant to choose between; the CSS-side
    # --transparent-opacity-light/heavy still controls how strong the
    # overlay looks on top of the live blur.
    if not sys.platform.startswith("win"):
        return
    if not hwnd:
        raise RuntimeError("no hwnd")

    dwmapi = ctypes.windll.dwmapi

    DWM_BB_ENABLE = 0x1
    bb = _DWM_BLURBEHIND(
        dwFlags=DWM_BB_ENABLE, fEnable=1 if backdrop_type != 1 else 0,
        hRgnBlur=None, fTransitionOnMaximized=0,
    )
    hr = dwmapi.DwmEnableBlurBehindWindow(ctypes.c_void_p(hwnd), ctypes.byref(bb))
    if hr != 0:
        raise RuntimeError(f"DwmEnableBlurBehindWindow failed, hr={hr:#x}")

    margins = _MARGINS(-1, -1, -1, -1)
    dwmapi.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(margins))

    # pywebview's own BrowserForm.__init__ (platforms/winforms.py,
    # update_title_bar_theme) unconditionally sets DWMWA_SYSTEMBACKDROP_TYPE
    # to DWMSBT_MAINWINDOW (2 = Mica) on dark systems the moment the native
    # HWND is constructed inside webview.start() — BEFORE this function ever
    # gets a chance to run (our own pre-start _enable_acrylic_blur call always
    # fails silently, window.native doesn't exist yet at that point; this
    # function's first real invocation is from _reveal(), well after Mica is
    # already sitting on the window). We never used to touch attribute 38 at
    # all, so Mica stayed active the whole time, layered underneath our own
    # DwmEnableBlurBehindWindow call. Mica has its own delayed material-
    # realization tick (same delay noted in the original Mica-vs-Acrylic
    # investigation) — blur-behind paints the real desktop instantly, then a
    # beat later DWM finishes realizing Mica on top of it, recoloring
    # everything with its fixed blue-grey recipe. That's the exact "clean
    # flash, then blue creeps in" behavior reported live. Explicitly forcing
    # DWMSBT_NONE here kills Mica for good so blur-behind is the only
    # backdrop mechanism ever active on this HWND, regardless of level.
    DWMWA_SYSTEMBACKDROP_TYPE = 38
    DWMSBT_NONE = 1
    backdrop_none = ctypes.c_int(DWMSBT_NONE)
    dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
        ctypes.byref(backdrop_none), ctypes.sizeof(backdrop_none),
    )


_backdrop_guard_installed = False


def _guard_backdrop_theme(hwnd):
    # pywebview's BrowserForm subscribes Microsoft.Win32.SystemEvents.
    # UserPreferenceChanged -> update_title_bar_theme() for the *entire*
    # lifetime of the window (platforms/winforms.py __init__), and that
    # handler unconditionally reasserts Mica (DWMWA_SYSTEMBACKDROP_TYPE=2)
    # on dark systems every single time that event fires — not just on an
    # actual theme change, Windows fires UserPreferenceChanged for other
    # system preference tweaks too. Without a counter-handler, Mica could
    # silently creep back over our live blur-behind mid-session, not only
    # at startup. .NET multicast delegates invoke subscribers in the order
    # they were added via +=; pywebview subscribes at window construction,
    # long before this ever runs, so subscribing here guarantees our
    # handler always runs last and wins the last-write race.
    global _backdrop_guard_installed
    if _backdrop_guard_installed:
        return
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from Microsoft.Win32 import SystemEvents

        def _on_pref_changed(sender, e):
            try:
                DWMWA_SYSTEMBACKDROP_TYPE = 38
                DWMSBT_NONE = 1
                backdrop_none = ctypes.c_int(DWMSBT_NONE)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
                    ctypes.byref(backdrop_none), ctypes.sizeof(backdrop_none),
                )
            except Exception:
                pass

        SystemEvents.UserPreferenceChanged += _on_pref_changed
        _backdrop_guard_installed = True
    except Exception as e:
        _write(f"\n  backdrop theme guard failed: {e!r}\n")


def _strip_layered_style(hwnd):
    # pywebview's own WinForms backend toggles Form.Opacity (0 -> 1) as part
    # of its hidden-window creation dance (platforms/winforms.py,
    # create_window: `if window.hidden: browser.Opacity = 0; ...; Opacity =
    # 1`). WinForms implements Opacity via SetLayeredWindowAttributes with
    # LWA_ALPHA — a GDI whole-window alpha-blend redirection surface, a
    # completely different code path from the WS_EX_LAYERED+colorkey
    # (-transparentcolor) trick the working bare test window used. Once
    # .NET has touched Opacity, WS_EX_LAYERED tends to stay set on the HWND
    # even after Opacity is reset to 1 (fully opaque) — and that leftover
    # layered redirection surface sits between WebView2's own real alpha
    # channel (DirectComposition) and DWM's live blur-behind compositing,
    # which is the suspected source of the residual blue tint (the test
    # window, which never touched Opacity at all, showed the real desktop
    # with zero tint). We never need Form.Opacity again after creation, so
    # it's safe to just rip the WS_EX_LAYERED bit back off here and let
    # WebView2's own alpha + DwmEnableBlurBehindWindow composite directly.
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED — required by
    # MSDN whenever GWL_EXSTYLE changes at runtime. Skipping this left the
    # window with WS_VISIBLE cleared entirely (confirmed live via EnumWindows:
    # IsWindowVisible=False right after the bare SetWindowLongW call) instead
    # of just losing the layered redirection surface — USER32 desyncs the
    # window's visible state when the style bit changes without a frame
    # refresh. Re-showing afterward is a deliberate belt-and-suspenders: it
    # forces WS_VISIBLE back on regardless of that desync.
    SWP_FLAGS = 0x2 | 0x1 | 0x4 | 0x20
    user32 = ctypes.windll.user32
    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style & ~WS_EX_LAYERED)
    user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)


_TRANSPARENT_LEVELS = {"off": 1, "light": 2, "heavy": 3}
_TRANSPARENT_PREF_PATH = os.path.join(
    os.getenv("LOCALAPPDATA", os.path.expanduser("~")), "KikiHub", "transparent_level.txt"
)


def _read_transparent_pref():
    try:
        with open(_TRANSPARENT_PREF_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "off"


def _save_transparent_pref(level):
    try:
        os.makedirs(os.path.dirname(_TRANSPARENT_PREF_PATH), exist_ok=True)
        with open(_TRANSPARENT_PREF_PATH, "w", encoding="utf-8") as f:
            f.write(level)
    except OSError:
        pass


def _enable_acrylic_blur(window):
    # Applies the LAST-SAVED level directly, not a hardcoded "off" — booting
    # into None and having JS switch to the real (e.g. heavy/Acrylic) level a
    # moment later meant every launch showed a visible none-to-tinted
    # transition once the window was revealed (DWM's backdrop-material swap
    # has its own brief realization delay, even though the API call itself
    # completes while the window is still hidden). Applying the final level
    # once, while still hidden, gives DWM the whole warm-up window (Flask
    # start + donut spin) to fully realize the material before anything is
    # ever shown, instead of realizing it live in front of the user.
    hwnd = _window_hwnd(window)
    if not hwnd:
        raise RuntimeError("window.native not available yet")
    level = _read_transparent_pref()
    _apply_acrylic(hwnd, _TRANSPARENT_LEVELS.get(level, 1))


def _bring_existing_instance_to_front():
    hwnd = ctypes.windll.user32.FindWindowW(None, "KikiHub")
    if not hwnd:
        return False
    SW_RESTORE = 9
    ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    return True


def _acquire_single_instance_lock():
    # Global\\ prefix (not Local\\) so the mutex is visible across sessions —
    # otherwise a copy launched elevated/from a different session could slip
    # past this check and spawn a second tray icon anyway.
    ERROR_ALREADY_EXISTS = 183
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\KikiHubSingleInstance")
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    if sys.platform.startswith("win") and not _acquire_single_instance_lock():
        # Another KikiHub is already running (possibly hidden in the tray) —
        # surface it instead of piling up a second process + tray icon.
        _bring_existing_instance_to_front()
        sys.exit(0)

    _enable_vt_mode()
    _size_canvas_to_console()
    _write(BANNER)

    threading.Thread(target=_run_flask,             daemon=True).start()
    threading.Thread(target=_create_desktop_shortcut, daemon=True).start()

    # Donut runs in background — main thread is free for webview immediately.
    threading.Thread(target=_run_donut, daemon=True).start()

    # Create the window NOW with a blank page so WebView2 warms up in parallel
    # with Flask startup and the donut animation (instead of after both finish).
    # transparent=True costs nothing when unused: as long as the page paints
    # an opaque background (the default), WebView2 fully occludes the Form
    # underneath and nothing changes visually. It only matters once the CSS
    # side deliberately makes body semi-transparent (Settings toggle) — at
    # that point _enable_acrylic_blur below is what's actually showing
    # through: the real desktop, blurred, not just an empty black Form.
    window = webview.create_window(
        "KikiHub",
        html="<body style='background:#04111a;margin:0;padding:0'></body>",
        width=1280, height=860, min_size=(900, 600),
        background_color=BG_COLOR, js_api=Api(), hidden=True, transparent=True,
    )
    try:
        _enable_acrylic_blur(window)
    except Exception as e:
        _write(f"\n  acrylic blur unavailable: {e!r} (window still works, just opaque)\n")

    # As soon as Flask answers, navigate the already-warm window to the app.
    threading.Thread(target=_navigate_when_ready, args=(window,), daemon=True).start()

    # Show window once the real page has loaded AND the donut has run ≥5s.
    window.events.loaded += lambda: threading.Thread(
        target=_on_loaded, args=(window,), daemon=True
    ).start()
    window.events.closing += _on_closing

    # Fallback in case the loaded event never fires a second time (see
    # _reveal_watchdog docstring-comment) — otherwise the window stays
    # hidden and the console stays up forever.
    threading.Thread(target=_reveal_watchdog, args=(window,), daemon=True).start()

    # private_mode defaults to True in pywebview (incognito-style WebView2
    # profile) — every launch got a throwaway profile, so localStorage
    # (theme, accent, rail state, saved API keys) silently reset each time.
    # An explicit storage_path under %LOCALAPPDATA% keeps the same profile
    # across launches so settings actually persist.
    import os
    storage_path = os.path.join(os.getenv("LOCALAPPDATA", os.path.expanduser("~")), "KikiHub", "webview2")
    webview.start(private_mode=False, storage_path=storage_path)

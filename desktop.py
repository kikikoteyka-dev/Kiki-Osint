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
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
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
        window.show()
        time.sleep(0.05)   # let the window actually paint before console vanishes
        _hide_console()
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


if __name__ == "__main__":
    _enable_vt_mode()
    _size_canvas_to_console()
    _write(BANNER)

    threading.Thread(target=_run_flask,             daemon=True).start()
    threading.Thread(target=_create_desktop_shortcut, daemon=True).start()

    # Donut runs in background — main thread is free for webview immediately.
    threading.Thread(target=_run_donut, daemon=True).start()

    # Create the window NOW with a blank page so WebView2 warms up in parallel
    # with Flask startup and the donut animation (instead of after both finish).
    window = webview.create_window(
        "KikiHub",
        html="<body style='background:#04111a;margin:0;padding:0'></body>",
        width=1280, height=860, min_size=(900, 600),
        background_color=BG_COLOR, js_api=Api(), hidden=True,
    )

    # As soon as Flask answers, navigate the already-warm window to the app.
    threading.Thread(target=_navigate_when_ready, args=(window,), daemon=True).start()

    # Show window once the real page has loaded AND the donut has run ≥5s.
    window.events.loaded += lambda: threading.Thread(
        target=_on_loaded, args=(window,), daemon=True
    ).start()

    # Fallback in case the loaded event never fires a second time (see
    # _reveal_watchdog docstring-comment) — otherwise the window stays
    # hidden and the console stays up forever.
    threading.Thread(target=_reveal_watchdog, args=(window,), daemon=True).start()

    webview.start()

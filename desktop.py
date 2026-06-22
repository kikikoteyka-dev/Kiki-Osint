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


def _run_flask():
    # Imported here (not at module scope) so the slow module-level work in
    # app.py — DNS probing for the Gemini API host — runs in this thread
    # instead of blocking the main thread before the loading screen appears.
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
_W, _H = 60, 22


def _donut_frame(a, b):
    cos_a, sin_a = math.cos(a), math.sin(a)
    cos_b, sin_b = math.cos(b), math.sin(b)
    out = [" "] * (_W * _H)
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

            xp = int(_W / 2 + 18 * ooz * x)
            yp = int(_H / 2 - 9 * ooz * y)

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
                    out[idx] = _LUMINANCE[min(int(lum * 8), len(_LUMINANCE) - 1)]
            phi += 0.07
        theta += 0.07
    return "\n".join("".join(out[r * _W:(r + 1) * _W]) for r in range(_H))


def _wait_for_server(timeout=30):
    url = f"http://{HOST}:{PORT}/"
    deadline = time.time() + timeout
    a = b = 0.0
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            _write("\x1b[2J\x1b[H  ready.\n")
            return True
        except Exception:
            frame = _donut_frame(a, b)
            _write(f"\x1b[2J\x1b[H{frame}\n  starting backend...\n")
            a += 0.18
            b += 0.09
            time.sleep(0.05)
    _write("\x1b[2J\x1b[H  ! backend did not respond in time\n")
    return False


if __name__ == "__main__":
    _enable_vt_mode()
    _write(BANNER)
    threading.Thread(target=_run_flask, daemon=True).start()
    threading.Thread(target=_create_desktop_shortcut, daemon=True).start()
    _wait_for_server()
    _hide_console()
    webview.create_window(
        "KikiHub", f"http://{HOST}:{PORT}/",
        width=1280, height=860, min_size=(900, 600),
        background_color=BG_COLOR,
    )
    webview.start()

# Builds the standalone KikiHub desktop app.
#
# --onedir (not --onefile): a single-file build self-extracts to a fresh %TEMP%
# folder on EVERY launch (~6s, every time). --onedir pays a one-time Defender
# scan on a machine's first-ever launch (~30s) and then starts in <1s on every
# launch after that — far better for an app a user opens repeatedly.
#
# Run from the project root. Output lands in dist\KikiHub\ — zip that folder
# for distribution (KikiHub.exe must stay next to its _internal folder).
#
# maigret (search_maigret() calls the library in-process, not via subprocess):
# --collect-all maigret pulls in its resources\data.json (the ~3000-site lookup
# database) and resources\settings.json, neither of which PyInstaller's static
# analysis would find on its own since they're read as package data at runtime,
# not imported. --collect-all also grabs maigret's own submodules (checking,
# sites, executors, report, web\...) that a plain --hidden-import would miss.
# The same treatment goes to maigret's own dependencies that ship no PyInstaller
# hook of their own and would otherwise silently drop code or data in the frozen
# build: socid_extractor (account-ID parsing, imported directly by maigret.py),
# aiodns (async DNS resolution used by checking.py; wraps the compiled pycares
# extension), aiohttp_socks (SOCKS/proxy connector used by checking.py and
# submit.py), and cloudscraper (Cloudflare-bypass client used by submit.py,
# which ships a user_agent\browsers.json data file it reads at runtime).
# NOTE: pyinstaller-hooks-contrib ships a hook for cloudscraper that collects
# its data files, but not its hidden imports — --collect-all is added anyway
# for the same belt-and-suspenders reason webview/pystray get it above despite
# also having hooks. aiohttp itself is left alone: it's statically imported by
# maigret with no dynamic/string-based imports of its own, so PyInstaller's
# normal analysis already follows it (and its compiled _http_parser/_http_writer
# extensions) without help. maigret's report-only deps (pycountry, pyvis,
# xhtml2pdf, reportlab, xmind, jinja2) are intentionally left out — they're only
# reachable from maigret's HTML/PDF/XMind report generators, not from the
# search path search_maigret() calls; add them the same way if that changes.

pyinstaller --onedir --console --name KikiHub --noconfirm --clean `
  --add-data "frontend;frontend" `
  --add-data "index.html;." `
  --add-data "kiki_logo.png;." `
  --add-data "hashcat_logo.png;." `
  --add-data "flipper_logo.png;." `
  --collect-all webview `
  --collect-all pystray `
  --hidden-import pystray._win32 `
  --hidden-import vk_module `
  --hidden-import keys_store `
  --hidden-import dns.query `
  --hidden-import dns.message `
  --hidden-import dns.rdatatype `
  --hidden-import serial `
  --hidden-import serial.tools.list_ports `
  --hidden-import piexif `
  --collect-all maigret `
  --collect-all socid_extractor `
  --collect-all aiodns `
  --collect-all aiohttp_socks `
  --collect-all cloudscraper `
  --exclude-module matplotlib `
  --exclude-module PyQt5 `
  --exclude-module PySide2 `
  --exclude-module PySide6 `
  --exclude-module PyQt6 `
  --exclude-module tkinter `
  --exclude-module scipy `
  --exclude-module pandas `
  desktop.py

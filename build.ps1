# Builds the standalone KikiHub desktop app.
#
# --onedir (not --onefile): a single-file build self-extracts to a fresh %TEMP%
# folder on EVERY launch (~6s, every time). --onedir pays a one-time Defender
# scan on a machine's first-ever launch (~30s) and then starts in <1s on every
# launch after that — far better for an app a user opens repeatedly.
#
# Run from the project root. Output lands in dist\KikiHub\ — zip that folder
# for distribution (KikiHub.exe must stay next to its _internal folder).

pyinstaller --onedir --console --name KikiHub --noconfirm `
  --add-data "frontend;frontend" `
  --add-data "index.html;." `
  --add-data "kiki_logo.png;." `
  --add-data "hashcat_logo.png;." `
  --add-data "flipper_logo.png;." `
  --collect-all webview `
  --hidden-import vk_module `
  --hidden-import keys_store `
  --hidden-import dns.query `
  --hidden-import dns.message `
  --hidden-import dns.rdatatype `
  --hidden-import serial `
  --hidden-import serial.tools.list_ports `
  --hidden-import piexif `
  --exclude-module matplotlib `
  --exclude-module PyQt5 `
  --exclude-module PySide2 `
  --exclude-module PySide6 `
  --exclude-module PyQt6 `
  --exclude-module tkinter `
  --exclude-module scipy `
  --exclude-module pandas `
  desktop.py

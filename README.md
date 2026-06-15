# KikiHub

🇬🇧 **English** | [🇷🇺 Русский](README.ru.md)

> Local web hub for OSINT, Wi-Fi handshake cracking, and Flipper Zero file management — all in one interface.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows-0078d7?style=flat-square)

> [!WARNING]
> **LEGAL DISCLAIMER**
>
> This tool is provided for **educational purposes and authorized security testing only**.
>
> - ✅ Use only on networks and devices **you own** or have **explicit written permission** to test
> - ❌ Using this tool against networks without authorization is **illegal** in most jurisdictions (including Russia — Article 272-274 of the Criminal Code)
> - ❌ The author takes **no responsibility** for any misuse, damage, or legal consequences arising from use of this software
> - ❌ Capturing, cracking, or accessing Wi-Fi networks without consent is a criminal offence
>
> By using KikiHub you confirm that you have the legal right to test the target systems.

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [API Keys](#api-keys)
- [Flipper Zero](#flipper-zero)
- [Known Issues](#known-issues)
- [Disclaimer](#disclaimer)

---

## Features

| Tab | Description |
|-----|--------------|
| 🔍 **OSINT** | Username / email → digital portrait via VK, Maigret (500+ sites), HaveIBeenPwned, AI analysis |
| 📶 **WiFi Cracker** | Drop a `.pcap` → convert → crack with hashcat. Multi-SSID, real-time terminal output |
| 🐬 **Flipper Zero** | Browse the SD card over USB, download `.pcap` files straight to your PC with EAPOL check |
| ⬇️ **Downloader** | Paste a video URL (YouTube, TikTok, etc.) → fetch metadata → download as MP4 or extract as MP3 (powered by yt-dlp) |
| 📍 **GEOINT** | Upload a photo → extract GPS coordinates from EXIF, with optional AI visual analysis to guess the location |

---

## Quick Start

For people who already know what they're doing — short checklist (details in [Installation](#installation)):

1. `git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git -b kiki-hub` (anywhere)
2. `pip install Flask flask-cors requests httpx beautifulsoup4 python-dotenv phonenumbers dnspython maigret holehe pyserial yt-dlp Pillow`
3. Hashcat → `C:\HashCat\hashcat-7.1.2\` + WSL with `hcxtools` (for pcap conversion)
4. Copy `wifi_cracker/wifi_cracker.py` from the repo to `C:\HashCat\hashcat-7.1.2\wifi_cracker.py`
5. Enter your API keys via the **Settings** tab (creates `keys.json` automatically)
6. `python app.py` → open **http://localhost:7777**

---

## Project Structure

`app.py` detects its own folder automatically, so the repo can be cloned anywhere with no path edits needed.

```
osint-portrait\
    app.py              ← main app, entry point (python app.py)
    index.html          ← hub shell (header + tabs)
    keys_store.py       ← reads/writes keys.json
    vk_module.py        ← VK OSINT module
    frontend\           ← OSINT panel (served at /osint/)
    wifi_cracker\        ← WiFi Cracker panel (versioned copy)
```

Hashcat itself lives outside the repo at `C:\HashCat\hashcat-7.1.2\` — see [Installation](#installation).

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git -b kiki-hub C:\Users\<you>\osint-portrait
cd C:\Users\<you>\osint-portrait
```

The path can be anything — `app.py` detects its own folder.

### 2. Python dependencies

```bash
pip install Flask flask-cors requests httpx beautifulsoup4 python-dotenv phonenumbers dnspython maigret holehe pyserial yt-dlp Pillow
```

For AI analysis in OSINT (optional, install only what you plan to use):

```bash
pip install google-genai anthropic
```

### 3. Hashcat

Download from [hashcat.net](https://hashcat.net/hashcat/) → extract into `C:\HashCat\hashcat-7.1.2\`

Check that your GPU is detected:
```bash
C:\HashCat\hashcat-7.1.2\hashcat.exe -I
```

For NVIDIA — install the [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) (~3x faster than OpenCL).

### 4. WSL + hcxpcapngtool

WSL is needed to convert `.pcap` → `.hc22000`. **Important:** `app.py` calls plain `wsl ...`
without `-d <distro>` — so `hcxpcapngtool` must be installed in your **default** distro
(the one marked `*` in `wsl -l -v`). Conversion goes through `/tmp` inside WSL (stdin/stdout),
so the default distro's `/mnt/c` isn't even used — but if your default is, say, `docker-desktop`
while you installed `hcxtools` in `Ubuntu`, conversion will silently fail
(`hcxpcapngtool: command not found`) and EAPOL/PMKID will always read `0`.

```bash
# In PowerShell (as administrator)
wsl --install

# Check which distro is default (marked with *)
wsl -l -v

# If needed, set the default:
wsl --set-default Ubuntu

# In WSL (the DEFAULT distro!)
sudo apt update && sudo apt install hcxtools -y

# Verify — should work WITHOUT -d
wsl hcxpcapngtool --version
```

### 5. Wordlists

Drop `.txt` files into `C:\HashCat\hashcat-7.1.2\` — KikiHub will pick them up automatically.

Recommended:
- `rockyou.txt` — the classic, 14M passwords
- `weakpass_4.latin.txt` — [weakpass.com](https://weakpass.com), 2B+ passwords (22 GB)

### 6. Run

```bash
python app.py
```

Open **http://localhost:7777**

---

## API Keys

Open the **Settings** tab in the UI and paste your keys there — `keys.json` is created
automatically next to `app.py` (it's gitignored, so it stays local and is never committed).

| Key | Where to get it |
|-----|------------------|
| VK Token | [vkhost.github.io](https://vkhost.github.io) → Kate Mobile |
| Gemini | [aistudio.google.com](https://aistudio.google.com) |
| Claude | [console.anthropic.com](https://console.anthropic.com) |
| HaveIBeenPwned | [haveibeenpwned.com/API/Key](https://haveibeenpwned.com/API/Key) |

> 💡 **Gemini is recommended** for AI analysis in OSINT — it has the most generous free tier.

---

## Flipper Zero

- Connect over USB, close qFlipper (it holds the COM port)
- Pick the port from the dropdown (usually COM4)
- The browser shows the SD card's file system
- **↓** — download the file to your browser (checks EAPOL for pcaps)
- **🔓** — send the pcap directly to WiFi Cracker

---

## Known Issues

**`hcxpcapngtool: command not found`, EAPOL/PMKID always 0**
→ `hcxtools` isn't installed in WSL's default distro. Check `wsl -l -v` and set the right distro
as default with `wsl --set-default <name>` (see [WSL + hcxpcapngtool](#installation)).

**Gemini API: "DNS error" / `generativelanguage.googleapis.com` doesn't resolve**
→ A known DNS resolution issue on some networks. KikiHub works around it automatically by
resolving the host via xbox-dns.ru's DoH service and connecting to that IP with the correct
SNI. If the error persists, check your internet connection.

**Gemini API: "User location is not supported"**
→ Google's regional geo-block (e.g. for IPs from Russia). KikiHub works around this
automatically the same way as the DNS issue above — by routing Gemini requests through an
xbox-dns.ru-resolved IP that isn't geo-blocked. If you still see this error, restart the app
(the workaround resolves once at startup and can occasionally fail on a flaky connection).

**Flipper not detected / port won't open**
→ Close qFlipper and any other program holding the COM port. Check that `pip install pyserial` ran.

---

## Disclaimer

For educational purposes and authorized security testing only.

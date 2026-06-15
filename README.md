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

- [What's new in v3.0](#whats-new-in-v30)
- [Features](#features)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [API Keys](#api-keys)
- [Flipper Zero](#flipper-zero)
- [Known Issues](#known-issues)
- [Disclaimer](#disclaimer)

---

## What's new in v3.0

- **Fully portable** — `app.py` now auto-detects its own folder, so the repo can be cloned anywhere
  without editing paths (previously required setting `OSINT_DIR`)
- **OSINT panel and VK module are now part of the repo** — `frontend/` and `vk_module.py` are
  versioned alongside the project, no separate external folder needed
- **Collapsible bottom dock** — a chevron above the dock hides/shows the tab bar
  (OSINT / WiFi Cracker / Flipper / Settings) with smooth slide/fade animations; state persists
  between sessions
- **OSINT results close button** repositioned to the top-left corner, no longer overlaps other
  UI elements
- **Gemini API** — SNI-based DNS workaround for `generativelanguage.googleapis.com`, plus clear
  error messages for DNS/geo-block issues
- Settings: **Gemini API marked as Recommended** as the default free option

---

## Features

| Tab | Description |
|-----|--------------|
| 🔍 **OSINT** | Username / email → digital portrait via VK, Maigret (500+ sites), HaveIBeenPwned, AI analysis |
| 📶 **WiFi Cracker** | Drop a `.pcap` → convert → crack with hashcat. Multi-SSID, real-time terminal output |
| 🐬 **Flipper Zero** | Browse the SD card over USB, download `.pcap` files straight to your PC with EAPOL check |

---

## Quick Start

For people who already know what they're doing — short checklist (details in [Installation](#installation)):

1. `git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git -b kiki-hub` (anywhere)
2. `pip install Flask flask-cors requests httpx beautifulsoup4 python-dotenv phonenumbers dnspython maigret holehe pyserial`
3. Hashcat → `C:\HashCat\hashcat-7.1.2\` + WSL with `hcxtools` (for pcap conversion)
4. Copy `wifi_cracker/wifi_cracker.py` from the repo to `C:\HashCat\hashcat-7.1.2\wifi_cracker.py`
5. Create `keys.json` next to `app.py`, or enter keys via the Settings tab
6. `python app.py` → open **http://localhost:7777**

---

## Project Structure

**The repo is fully portable** — `app.py` detects its own folder automatically
(`BASE_DIR = os.path.dirname(os.path.abspath(__file__))`), so it can be cloned anywhere with no
path edits needed.

The only hardcoded path is **hashcat** (constant `BASE`, app.py line 42), which lives outside
the repo:

```
<repo>\                            ← clone anywhere
    app.py
    index.html                     ← hub shell (header + 3 tabs)
    keys_store.py                  ← reads keys.json (created separately, not in git)
    vk_module.py                   ← VK OSINT module
    flipper_logo.png
    kiki_logo.png
    hashcat_logo.png
    DISCLAIMER.md
    frontend\
        index.html                 ← OSINT panel (served at /osint/)
        kiki_logo.png
    wifi_cracker\
        wifi_cracker.py            ← versioned copy — the LIVE copy must live at
                                       C:\HashCat\hashcat-7.1.2\wifi_cracker.py (see below)
    temp\                          ← created automatically (temp files)

C:\HashCat\hashcat-7.1.2\         ← hashcat must be HERE (constant BASE, app.py line 42)
    hashcat.exe
    wifi_cracker.py                ← copy from wifi_cracker\wifi_cracker.py in the repo —
                                       app.py reads the panel's embedded HTML from this file
                                       (line 814) and launches it as a process (line ~1068)
    hashes\                        ← .hc22000 / .pcap files (created automatically)
    rockyou.txt                    ← wordlists go here
    weakpass_4.latin.txt           ← and other .txt files
```

> If your hashcat path differs — change `BASE` (line 42), `wc_path` (line ~814) and the path to
> `wifi_cracker.py` in `start_wificrack()` (line ~1068) in `app.py`.

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
pip install Flask flask-cors requests httpx beautifulsoup4 python-dotenv phonenumbers dnspython maigret holehe pyserial
```

For AI analysis in OSINT (optional, install only what you plan to use):

```bash
pip install google-genai anthropic openai
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

Create `keys.json` next to `app.py` (not tracked by git):

```json
{
  "VK_TOKEN": "vk1.a.xxx",
  "GEMINI_API_KEY": "AIza...",
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "OPENAI_API_KEY": "sk-..."
}
```

Or set them directly from the **Settings** tab in the UI — it also lets you verify the keys were saved.

| Key | Where to get it |
|-----|------------------|
| VK Token | [vkhost.github.io](https://vkhost.github.io) → Kate Mobile |
| Gemini | [aistudio.google.com](https://aistudio.google.com) |
| Claude | [console.anthropic.com](https://console.anthropic.com) |
| ChatGPT | [platform.openai.com](https://platform.openai.com) |
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
→ A known DNS resolution issue on some networks. KikiHub works around it automatically via an
SNI trick (resolves `www.googleapis.com` but connects with the correct SNI). If the error
persists, check your internet connection.

**Gemini API: "User location is not supported"**
→ Google's regional geo-block (e.g. for IPs from Russia). You need a VPN exiting outside the
blocked region — this is a restriction on Google's side and can't be fixed in code.

**Flipper not detected / port won't open**
→ Close qFlipper and any other program holding the COM port. Check that `pip install pyserial` ran.

---

## Disclaimer

For educational purposes and authorized security testing only.

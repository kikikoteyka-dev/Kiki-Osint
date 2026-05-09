# KikiHub — Personal OSINT & WiFi Hub

A unified local web interface combining OSINT tools, WiFi handshake cracking, and Flipper Zero file management.

## Structure

```
kiki_hub/
├── app.py              # Flask backend (port 7777)
├── index.html          # Main hub UI with dock navigation
├── keys_store.py       # API key management
└── wifi_cracker/
    └── wifi_cracker.py # WiFi Cracker Flask app (port 5555)
```

## Components

### 🔍 Kiki OSINT
Full-featured OSINT portrait builder — username/email → social profiles, VK data, breach checks, AI analysis.

### 📡 WiFi Cracker
- Drag & drop `.pcap` files
- Converts via WSL `hcxpcapngtool` → `.hc22000`
- Runs `hashcat -m 22000` with multiple wordlists
- Shows SSID/BSSID/password in real-time terminal UI
- Supports multiple networks per capture file

### 🐬 Flipper Zero
- Browse SD card contents over USB serial
- Download `.pcap` files directly to PC
- One-click file browser

## Requirements

```
Flask
pyserial
```

### External tools
- **hashcat** 7.x — GPU WPA cracking
- **hcxpcapngtool** — compiled in WSL Alpine
- **tshark** — EAPOL frame analysis
- **CUDA Toolkit** — for NVIDIA GPU acceleration

## Setup

```bash
pip install Flask pyserial
python app.py  # runs on http://localhost:7777
```

Place `wifi_cracker.py` in your hashcat directory and update paths in `app.py`.

## Keys (not included)
Create `keys.json` next to `app.py`:
```json
{
  "VK_TOKEN": "...",
  "GEMINI_API_KEY": "...",
  "ANTHROPIC_API_KEY": "...",
  "OPENAI_API_KEY": "..."
}
```

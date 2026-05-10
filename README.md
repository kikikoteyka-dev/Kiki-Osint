# KikiHub

> A local web hub combining OSINT research, WiFi handshake cracking, and Flipper Zero file management — all in one interface.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows-0078d7?style=flat-square)
![GPU](https://img.shields.io/badge/GPU-CUDA%20%2F%20OpenCL-76b900?style=flat-square)

---

## What's inside

| Tab | Description |
|-----|-------------|
| 🔍 **OSINT** | Username / email → full digital portrait via VK, Maigret (500+ sites), HaveIBeenPwned, AI analysis |
| 📡 **WiFi Cracker** | Drop a `.pcap` → convert → crack with hashcat. Multi-SSID, real-time terminal output |
| 🐬 **Flipper Zero** | Browse SD card over USB, download `.pcap` files directly to PC |

---

## Requirements

**Python packages**
```
pip install Flask flask-cors pyserial
```

**External tools**
| Tool | Purpose |
|------|---------|
| [hashcat 7.x](https://hashcat.net/hashcat/) | GPU WPA cracking (`-m 22000`) |
| WSL + hcxpcapngtool | Convert `.pcap` → `.hc22000` |
| [Wireshark](https://wireshark.org) | EAPOL frame analysis (tshark) |
| CUDA Toolkit | NVIDIA GPU acceleration (optional, ~3× speedup) |

**Wordlists** — place `.txt` files in `C:\HashCat\hashcat-7.1.2\`, KikiHub picks them all up automatically.
Recommended: [weakpass.com](https://weakpass.com) → `weakpass_4.txt`

---

## Setup

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git -b kiki-hub
cd Kiki-Osint
pip install Flask flask-cors pyserial

# Edit paths at the top of app.py to match your installation
python app.py
```

Open **http://localhost:7777**

---

## API Keys

Create `keys.json` next to `app.py` (never committed):

```json
{
  "VK_TOKEN": "vk1.a.xxx",
  "GEMINI_API_KEY": "AIza...",
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "OPENAI_API_KEY": "sk-..."
}
```

Or set them via the **Settings** tab in the UI.

| Key | Where to get |
|-----|-------------|
| VK Token | [vkhost.github.io](https://vkhost.github.io) → Kate Mobile |
| Claude | [console.anthropic.com](https://console.anthropic.com) |
| ChatGPT | [platform.openai.com](https://platform.openai.com) |
| Gemini | [aistudio.google.com](https://aistudio.google.com) |

---

## File structure

```
app.py                  Flask backend — hub routes + hashcat API + Flipper serial
index.html              Dock UI (OSINT / WiFi / Flipper / Settings panels)
keys_store.py           Read/write keys.json
wifi_cracker/
  wifi_cracker.py       Standalone WiFi Cracker app (also served via hub)
```

---

## WiFi cracking workflow

```
1. Capture with Flipper Zero + Marauder → sniffpmkid.pcap
2. Open WiFi Cracker tab → drag & drop pcap
3. Analyze → converts via WSL hcxpcapngtool
4. Run Hashcat → iterates wordlists automatically
5. Password appears in terminal when found
```

---

## Flipper Zero

Connect Flipper by USB, select COM port in the Flipper tab — browse and download files directly without qFlipper.

> Close qFlipper before connecting, it holds the COM port.

---

## Disclaimer

For educational and authorized testing purposes only.

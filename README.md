# KikiHub

Local unified web hub — OSINT, WiFi cracking, Flipper Zero file browser.

## Install
```
pip install Flask pyserial flask-cors
python app.py   # http://localhost:7777
```

## Structure
```
app.py              Flask backend (port 7777)
index.html          Dock UI
keys_store.py       API key storage
wifi_cracker/
  wifi_cracker.py   WiFi Cracker (port 5555)
```

## Requirements
- hashcat 7.x in C:\HashCat\hashcat-7.1.2\
- WSL with hcxpcapngtool
- Wireshark (tshark)
- CUDA Toolkit (optional, 3-4x speedup)

## Keys
Create keys.json next to app.py:
```json
{"VK_TOKEN":"...","GEMINI_API_KEY":"...","ANTHROPIC_API_KEY":"...","OPENAI_API_KEY":"..."}
```

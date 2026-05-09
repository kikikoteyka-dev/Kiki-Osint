:gb: English | [:ru: Russian](README_RU.md)

# Kiki OSINT — Digital Footprint Discovery Tool

An open-source web tool for gathering public data by **username**, **email**, or both at once.  
Supports VK, Telegram, GitHub, 500+ websites via Maigret, email checks, HaveIBeenPwned, and AI-generated portraits.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Projects in this repo

### 🔍 Kiki OSINT (`/`)
Full-featured OSINT portrait builder.

### 🛠️ KikiHub (`/kiki_hub/`)
Local unified web hub combining OSINT, WiFi cracking, and Flipper Zero file management in one interface with dock navigation.
→ [See KikiHub README](kiki_hub/README.md)

---

## Kiki OSINT Features

- 🔎 **Username** — VK, Telegram, GitHub, 500+ sites (Maigret)
- 📧 **Email** — registration check (Holehe) + breach lookup (HaveIBeenPwned) + Gravatar
- 🔀 **Both mode** — username + email simultaneously
- 🤖 **AI Portrait** — Claude, ChatGPT, or Gemini
- 📤 **Export** — JSON / TXT
- 🌍 **EN/RU** interface
- ⚡ **Real-time streaming** — SSE

---

## Installation

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git
cd Kiki-Osint
pip install -r requirements.txt
python app.py
```

Open: **http://localhost:5000**

---

## API Keys

Click **Settings** in the app. Keys saved to local `keys.json`.

- **VK Token** — https://vkhost.github.io/ → Kate Mobile → copy `access_token`
- **Claude** — https://console.anthropic.com
- **ChatGPT** — https://platform.openai.com
- **Gemini** — https://aistudio.google.com
- **HaveIBeenPwned** — https://haveibeenpwned.com/API/Key

---

## Project structure

```
Kiki-Osint/
├── app.py              # Flask backend (port 5000)
├── vk_module.py        # VK API
├── keys_store.py       # Key storage
├── frontend/
│   └── index.html      # UI
├── kiki_hub/           # KikiHub unified interface
│   ├── app.py          # Flask backend (port 7777)
│   ├── index.html      # Hub UI with dock
│   └── wifi_cracker/
│       └── wifi_cracker.py
├── requirements.txt
└── .gitignore
```

---

## Disclaimer

For legal purposes only: checking your own footprint, OSINT education, security testing with consent.

---

## License

MIT

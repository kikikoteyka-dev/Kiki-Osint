:gb: English | [:ru: Russian](README_RU.md)

# 🔍 Kiki OSINT — Digital Footprint Discovery Tool

An open-source web tool for gathering public data by **username** or **email**.  
Supports search across VK, Telegram, 500+ websites via Maigret, email checks via Holehe and HaveIBeenPwned, and AI-generated portraits via Claude, ChatGPT, or Gemini.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 📸 Features

- 🔎 **Username** — search across VK, Telegram and 500+ sites (Maigret)
- 📧 **Email** — registration check (Holehe) + breach lookup (HaveIBeenPwned)
- 🤖 **AI Portrait** — analytical summary via Claude, ChatGPT, or Gemini
- 🌐 **Language switcher** — EN/RU interface toggle
- ⚡ **Real-time streaming** — results appear as they arrive (SSE)
- 💾 **Persistent API keys** — saved to local `keys.json`, survive server restarts

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git
cd Kiki-Osint
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# Linux / Mac:
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## 🔑 API Keys Setup

Click **«Settings»** in the app interface and enter your keys directly in the browser — no config files needed. Keys are saved automatically to a local `keys.json` file.

### VK Token

1. Open **https://vkhost.github.io/**
2. Select the **«Kate Mobile»** app
3. Click **«Allow»**
4. In the URL bar, find `access_token=` and copy everything up to `&`

> ⚠️ This token is tied to your VK account. Never share it with anyone.

### AI Portrait (optional)

Click **«Settings»** in the app and enter:

- **Provider**: `anthropic`, `openai`, or `google`
- **API Key**: your key

Where to get API keys:
- Claude: **https://console.anthropic.com** → API Keys
- ChatGPT: **https://platform.openai.com** → API Keys
- Gemini: **https://aistudio.google.com** → Get API key

### HaveIBeenPwned API Key (optional)

Required for email breach lookup. Get it at **https://haveibeenpwned.com/API/Key**

---

## ▶️ Running the app

```bash
python app.py
```

Open in your browser: **http://localhost:5000**

On first launch, the Settings modal opens automatically if no API keys are configured.

---

## 📂 Project structure

```
Kiki-Osint/
├── app.py              # Flask server, API endpoints
├── config.py           # Environment variable loader
├── vk_module.py        # VK API search
├── tg_module.py        # Telegram search (via t.me scraping)
├── maigret_module.py   # Maigret integration
├── sources/            # Additional data sources
├── frontend/
│   ├── index.html      # UI (EN/RU)
│   ├── layout2.css     # Styles
│   └── kiki_logo.png   # Logo
├── .env.example        # Environment variables template
└── .gitignore
```

---

## ⚠️ Disclaimer

This tool is intended **for legal purposes only**: checking your own digital footprint, OSINT education, and security testing with the subject's consent. Do not use it for stalking, surveillance, or violating anyone's privacy.

---

## 📝 License

MIT — free to use with attribution.

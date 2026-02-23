:gb: English | [:ru: Russian](README_RU.md)

# Kiki OSINT - Digital Footprint Discovery Tool

An open-source web tool for gathering public data by username, email, or phone number.
Supports VK, Telegram, 500+ websites via Maigret, Holehe, HaveIBeenPwned, and AI portraits via Claude, ChatGPT, or Gemini.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- Username - search across VK, Telegram and 500+ sites (Maigret)
- Email - registration check (Holehe) + breach lookup (HaveIBeenPwned)
- Phone - country, carrier, timezone, number format
- AI Portrait - analytical summary via Claude, ChatGPT, or Gemini
- Language switcher - EN/RU interface toggle
- Real-time streaming - results appear as they arrive (SSE)

---

## Installation

### 1. Clone the repository

    git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git
    cd Kiki-Osint

### 2. Create a virtual environment

    python -m venv venv
    # Windows: venv\Scripts\activate
    # Linux/Mac: source venv/bin/activate

### 3. Install dependencies

    pip install -r requirements.txt

---

## API Keys Setup

Copy the template:

    cp .env.example .env

Fill in .env:

    VK_TOKEN=your_token_here
    TG_API_ID=12345678
    TG_API_HASH=your_hash_here

### VK Token

1. Open https://vkhost.github.io/
2. Select Kate Mobile and click Allow
3. Copy access_token= value from the URL bar (up to the &)

This token is tied to your VK account. Never share it.

### Telegram API ID and Hash

1. Go to https://my.telegram.org
2. Select API development tools
3. Copy App api_id to TG_API_ID and App api_hash to TG_API_HASH

### First Telegram launch

    python auth.py

Enter your phone number (+7XXXXXXXXXX) and the code from Telegram.
This creates osint_session.session locally. Never upload it - it is already in .gitignore.

### AI Portrait (optional)

Click AI Config in the app, enter:
- Provider: anthropic, openai, or google
- API Key: your key

Keys:
- Claude: https://console.anthropic.com
- ChatGPT: https://platform.openai.com
- Gemini: https://aistudio.google.com

---

## Running

    python app.py

Open: http://localhost:5000

---

## Project structure

    Kiki-Osint/
    |-- app.py              Flask server, API endpoints
    |-- config.py           Environment variable loader
    |-- auth.py             One-time Telegram authorization
    |-- vk_module.py        VK API search
    |-- tg_module.py        Telegram search (Telethon)
    |-- maigret_module.py   Maigret integration
    |-- sources/            Additional data sources
    |-- frontend/
    |   |-- index.html      UI (EN/RU)
    |   |-- layout2.css     Styles
    |   |-- kiki_logo.png   Logo
    |-- .env.example        Environment variables template
    |-- README_RU.md        Russian version
    |-- .gitignore

---

## Disclaimer

For legal purposes only: checking your own digital footprint, OSINT education, security testing with consent. Do not use for stalking or violating privacy.

---

## License

MIT
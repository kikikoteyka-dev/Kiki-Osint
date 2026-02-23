# Kiki OSINT - Digital Footprint Discovery Tool

> [Russian version / Русская версия](README_RU.md)

An open-source web tool for gathering public data by **username**, **email**, or **phone number**.
Supports search across VK, Telegram, 500+ websites via Maigret, email checks via Holehe and HaveIBeenPwned, and AI-generated portraits via Claude, ChatGPT, or Gemini.

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

Copy the template and fill in your credentials:

    cp .env.example .env

Open .env and add your keys:

    VK_TOKEN=your_token_here
    TG_API_ID=12345678
    TG_API_HASH=your_hash_here

### How to get a VK Token

1. Open https://vkhost.github.io/
2. Select the Kate Mobile app
3. Click Allow
4. In the URL bar, find access_token= and copy everything up to &

> Warning: This token is tied to your VK account. Never share it with anyone.

### How to get Telegram API ID and Hash

1. Go to https://my.telegram.org
2. Sign in with your phone number
3. Select API development tools
4. Fill in the form (app name can be anything)
5. Copy App api_id to TG_API_ID and App api_hash to TG_API_HASH

### First Telegram launch (session authorization)

    python auth.py

Enter your phone number in the format +7XXXXXXXXXX, then the code from Telegram.
A file osint_session.session will be created - it stores your session locally.

> Warning: Never upload osint_session.session to a public repository! It is already listed in .gitignore.

### AI Portrait (optional)

Click AI Config in the app interface and enter:
- Provider: anthropic, openai, or google
- API Key: your key

Where to get API keys:
- Claude: https://console.anthropic.com - API Keys
- ChatGPT: https://platform.openai.com - API Keys
- Gemini: https://aistudio.google.com - Get API key

---

## Running the app

    python app.py

Open in your browser: http://localhost:5000

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
    |-- README_RU.md        Russian version of this file
    |-- .gitignore

---

## Disclaimer

This tool is intended for legal purposes only: checking your own digital footprint, OSINT education, and security testing with the subject's consent. Do not use it for stalking, surveillance, or violating anyone's privacy.

---

## License

MIT - free to use with attribution.
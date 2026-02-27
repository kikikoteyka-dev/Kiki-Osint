[:gb: English](README.md) | :ru: Русский

# 🔍 Kiki OSINT — Инструмент разведки цифрового следа

Веб-инструмент для сбора открытых данных по **username**, **email** или обоим сразу.  
Поддерживает поиск по VK, Telegram, GitHub, сотням сайтов через Maigret, проверку email через Holehe, HaveIBeenPwned и Gravatar, анализ домена через WHOIS, а также AI-аналитику через Claude, ChatGPT или Gemini.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 📸 Возможности

- 🔎 **Username** — поиск по VK, Telegram, GitHub и 500+ сайтам (Maigret)
- 📧 **Email** — регистрации (Holehe) + утечки (HaveIBeenPwned) + профиль Gravatar + GitHub аккаунт
- 🌐 **Domain Info** — MX-записи, WHOIS (регистратор, дата), IP, определение одноразовых адресов
- 🔀 **Both mode** — одновременный поиск username + email в одном интерфейсе
- 🤖 **AI-портрет** — аналитическая сводка через Claude, ChatGPT или Gemini
- 📤 **Экспорт** — Copy JSON / Export TXT / Export JSON после каждого поиска
- 🌍 **Переключатель языка** — интерфейс на русском и английском
- ⚡ **Потоковая передача** — результаты появляются в реальном времени (SSE)
- 💾 **Персистентные ключи** — сохраняются в локальный `keys.json`, не теряются при перезапуске

---

## 🚀 Установка

### 1. Клонируй репозиторий

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git
cd Kiki-Osint
```

### 2. Создай виртуальное окружение

```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# Linux / Mac:
source venv/bin/activate
```

### 3. Установи зависимости

```bash
pip install -r requirements.txt
```

---

## 🔑 Настройка API-ключей

Нажми **«Settings»** в интерфейсе приложения и введи ключи прямо в браузере — никаких конфиг-файлов не нужно. Ключи сохраняются автоматически в локальный `keys.json`.

При первом запуске Settings открывается автоматически, если API-ключи не настроены.

### VK Token

1. Открой **https://vkhost.github.io/**
2. Выбери приложение **«Kate Mobile»**
3. Нажми **«Разрешить»**
4. В адресной строке найди `access_token=` и скопируй значение до `&`

> ⚠️ Токен привязан к твоему аккаунту VK. Не передавай его никому.

### AI-портрет (опционально)

Нажми **«Settings»** и введи:

- **Provider**: `anthropic`, `openai` или `google`
- **API Key**: твой ключ

Получить ключи:
- Claude: **https://console.anthropic.com** → API Keys
- ChatGPT: **https://platform.openai.com** → API Keys
- Gemini: **https://aistudio.google.com** → Get API key

### HaveIBeenPwned API Key (опционально)

Нужен для проверки утечек email. Получить на **https://haveibeenpwned.com/API/Key**

> GitHub поиск работает без API ключей.

---

## ▶️ Запуск

```bash
python app.py
```

Открой в браузере: **http://localhost:5000**

---

## 📂 Структура проекта

```
Kiki-Osint/
├── app.py              # Flask-сервер, API-эндпоинты
├── config.py           # Загрузка переменных из .env
├── vk_module.py        # Поиск по VK API
├── tg_module.py        # Поиск по Telegram (через t.me)
├── maigret_module.py   # Интеграция с Maigret
├── keys_store.py       # Хранение ключей (keys.json)
├── sources/            # Дополнительные источники данных
├── frontend/
│   ├── index.html      # Интерфейс (EN/RU)
│   ├── layout2.css     # Стили
│   └── kiki_logo.png   # Логотип
├── .env.example        # Шаблон переменных окружения
└── .gitignore
```

---

## ⚠️ Важно

Инструмент предназначен **только для легальных целей**: проверка собственного цифрового следа, OSINT-обучение, тестирование безопасности с разрешения субъекта. Не используй для слежки или нарушения приватности других людей.

---

## 📝 Лицензия

MIT — свободное использование с указанием автора.

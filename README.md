# 🔍 Kiki OSINT — Digital Footprint Discovery Tool

Инструмент для разведки цифрового следа по **username**, **email** или **номеру телефона**.  
Поддерживает поиск по VK, Telegram, сотням сайтов через Maigret, email-сканирование через Holehe и HaveIBeenPwned, а также AI-аналитику через Claude или GPT.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 📸 Возможности

- 🔎 **Username** — поиск по VK, Telegram и 500+ сайтам (Maigret)
- 📧 **Email** — проверка регистраций (Holehe) + утечки (HaveIBeenPwned)
- 📱 **Телефон** — страна, оператор, часовой пояс, формат
- 🤖 **AI-портрет** — аналитическая сводка через Claude или ChatGPT
- ⚡ Потоковая передача результатов в реальном времени (SSE)

---

## 🚀 Установка

### 1. Клонируй репозиторий

```bash
git clone https://github.com/ВАШ_НИКНЕЙМ/osint-portrait.git
cd osint-portrait
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
pip install flask flask-cors vk-api telethon maigret holehe httpx beautifulsoup4 phonenumbers python-dotenv
```

---

## 🔑 Настройка API-ключей

Скопируй шаблон и заполни своими данными:

```bash
cp .env.example .env
```

Открой файл `.env` и вставь ключи:

```env
VK_TOKEN=ваш_токен_здесь
TG_API_ID=12345678
TG_API_HASH=ваш_хеш_здесь
```

---

### Где взять VK Token

1. Открой в браузере: **https://vkhost.github.io/**
2. Выбери приложение **«Kate Mobile»**
3. Нажми **«Разрешить»**
4. В адресной строке найди `access_token=` и скопируй всё значение до `&`

> ⚠️ Токен привязан к твоему аккаунту VK. Не передавай его никому.

---

### Где взять Telegram API ID и Hash

1. Зайди на **https://my.telegram.org**
2. Войди со своим номером телефона
3. Выбери **«API development tools»**
4. Заполни форму (название приложения — любое, например `osint`)
5. Скопируй `App api_id` → в `.env` как `TG_API_ID`
6. Скопируй `App api_hash` → в `.env` как `TG_API_HASH`

---

### Первый запуск Telegram (авторизация сессии)

Telegram требует одноразовой авторизации через номер телефона:

```bash
python auth.py
```

Введи номер в формате `+7XXXXXXXXXX`, затем код из Telegram.  
После этого создастся файл `osint_session.session` — он хранит сессию локально.

> ⚠️ Файл `osint_session.session` содержит авторизацию твоего аккаунта — **никогда не выкладывай его в публичный репозиторий!** Он уже добавлен в `.gitignore`.

---

### AI-портрет (опционально)

AI-блок работает без ключей, если не нужен. Если хочешь включить:

В интерфейсе приложения есть кнопка **«AI Config»** — там вводишь прямо в браузере:

- **Provider**: `anthropic` или `openai`
- **API Key**: твой ключ

Получить ключи:
- Claude: **https://console.anthropic.com** → API Keys
- ChatGPT: **https://platform.openai.com** → API Keys

---

## ▶️ Запуск

```bash
python app.py
```

Открой в браузере: **http://localhost:5000**

---

## 📂 Структура проекта

```
osint-portrait/
├── app.py              # Flask-сервер, API-эндпоинты
├── config.py           # Загрузка переменных из .env
├── auth.py             # Одноразовая авторизация Telegram
├── vk_module.py        # Поиск по VK API
├── tg_module.py        # Поиск по Telegram (через Telethon)
├── maigret_module.py   # Интеграция с Maigret
├── sources/            # Дополнительные источники
├── frontend/
│   ├── index.html      # Интерфейс
│   ├── layout2.css     # Стили
│   └── kiki_logo.png   # Логотип
├── .env.example        # Шаблон для переменных окружения
└── .gitignore
```

---

## ⚠️ Важно

- Этот инструмент предназначен **только для легальных целей**: проверка собственного цифрового следа, OSINT-обучение, тестирование безопасности с разрешения субъекта.
- Не используй для слежки, преследования или нарушения приватности других людей.
- Соблюдай [Terms of Service](https://vk.com/dev/rules) используемых API.

---

## 📝 Лицензия

MIT — свободное использование с указанием автора.

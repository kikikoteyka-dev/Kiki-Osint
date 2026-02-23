[:gb: English](README.md) | :ru: Русский

# Kiki OSINT - Инструмент разведки цифрового следа

Веб-инструмент для сбора открытых данных по username, email или номеру телефона.
Поиск по VK, Telegram, сотням сайтов через Maigret, проверка email через Holehe и HaveIBeenPwned, AI-аналитика через Claude, ChatGPT или Gemini.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Возможности

- Username - поиск по VK, Telegram и 500+ сайтам (Maigret)
- Email - проверка регистраций (Holehe) + утечки (HaveIBeenPwned)
- Телефон - страна, оператор, часовой пояс, формат
- AI-портрет - аналитическая сводка через Claude, ChatGPT или Gemini
- Переключатель языка - интерфейс на русском и английском
- Потоковая передача - результаты появляются в реальном времени (SSE)

---

## Установка

### 1. Клонируй репозиторий

    git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git
    cd Kiki-Osint

### 2. Создай виртуальное окружение

    python -m venv venv
    # Windows: venv\Scripts\activate
    # Linux/Mac: source venv/bin/activate

### 3. Установи зависимости

    pip install -r requirements.txt

---

## Настройка API-ключей

Скопируй шаблон:

    cp .env.example .env

Заполни .env:

    VK_TOKEN=ваш_токен_здесь
    TG_API_ID=12345678
    TG_API_HASH=ваш_хеш_здесь

### VK Token

1. Открой https://vkhost.github.io/
2. Выбери Kate Mobile, нажми Разрешить
3. Скопируй access_token= из адресной строки (до символа &)

Токен привязан к твоему аккаунту VK. Не передавай его никому.

### Telegram API

1. Зайди на https://my.telegram.org
2. Выбери API development tools
3. Скопируй App api_id -> TG_API_ID и App api_hash -> TG_API_HASH

### Первый запуск Telegram

    python auth.py

Введи номер (+7XXXXXXXXXX) и код из Telegram.
Создастся osint_session.session - никогда не выкладывай его, он уже в .gitignore.

### AI-портрет (опционально)

Нажми AI Config в интерфейсе, введи:
- Provider: anthropic, openai или google
- API Key: твой ключ

Получить ключи:
- Claude: https://console.anthropic.com
- ChatGPT: https://platform.openai.com
- Gemini: https://aistudio.google.com

---

## Запуск

    python app.py

Открой в браузере: http://localhost:5000

---

## Структура проекта

    Kiki-Osint/
    |-- app.py              Flask-сервер, API-эндпоинты
    |-- config.py           Загрузка переменных из .env
    |-- auth.py             Одноразовая авторизация Telegram
    |-- vk_module.py        Поиск по VK API
    |-- tg_module.py        Поиск по Telegram (Telethon)
    |-- maigret_module.py   Интеграция с Maigret
    |-- sources/            Дополнительные источники данных
    |-- frontend/
    |   |-- index.html      Интерфейс (EN/RU)
    |   |-- layout2.css     Стили
    |   |-- kiki_logo.png   Логотип
    |-- .env.example        Шаблон переменных окружения
    |-- README.md           English version
    |-- .gitignore

---

## Важно

Только для легальных целей: проверка собственного цифрового следа, OSINT-обучение, тестирование безопасности с разрешения субъекта.

---

## Лицензия

MIT
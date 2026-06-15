# KikiHub

[🇬🇧 English](README.md) | 🇷🇺 **Русский**

> Локальный веб-хаб для OSINT, взлома Wi-Fi хендшейков и управления файлами Flipper Zero — всё в одном интерфейсе.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows-0078d7?style=flat-square)

> [!WARNING]
> **ЮРИДИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ**
>
> Этот инструмент предназначен **только для образовательных целей и авторизованного тестирования безопасности**.
>
> - ✅ Используй только на сетях и устройствах, которые **принадлежат тебе**, или на которые есть **письменное разрешение** владельца
> - ❌ Использование без авторизации — **уголовное преступление** в большинстве стран, включая Россию (статьи 272–274 УК РФ)
> - ❌ Автор **не несёт ответственности** за любой ущерб и юридические последствия использования
> - ❌ Перехват, взлом или доступ к Wi-Fi сетям без согласия владельца — уголовное преступление
>
> Используя KikiHub, ты подтверждаешь, что имеешь законное право тестировать целевые системы.

---

## Содержание

- [Возможности](#возможности)
- [Быстрый старт](#быстрый-старт)
- [Структура проекта](#структура-проекта)
- [Установка](#установка)
- [API ключи](#api-ключи)
- [Flipper Zero](#flipper-zero)
- [Известные проблемы](#известные-проблемы)
- [Disclaimer](#disclaimer)

---

## Возможности

| Вкладка | Описание |
|---------|----------|
| 🔍 **OSINT** | Username / email → цифровой портрет через VK, Maigret (500+ сайтов), HaveIBeenPwned, AI-анализ |
| 📶 **WiFi Cracker** | Бросаешь `.pcap` → конвертация → взлом hashcat. Мульти-SSID, терминал в реальном времени |
| 🐬 **Flipper Zero** | Браузер SD-карты по USB, скачивание `.pcap` прямо на ПК с проверкой EAPOL |
| ⬇️ **Загрузчик** | Вставь ссылку на видео (YouTube, TikTok и т.д.) → получи метаданные → скачай как MP4 или извлеки MP3 (на базе yt-dlp) |
| 📍 **GEOINT** | Загрузи фото → извлечение GPS-координат из EXIF, опционально AI-анализ изображения для определения локации |

---

## Быстрый старт

Для тех, кто уже знает что делает — короткий чек-лист (подробности в [Установке](#установка)):

1. `git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git -b kiki-hub` (куда угодно)
2. `pip install Flask flask-cors requests httpx beautifulsoup4 python-dotenv phonenumbers dnspython maigret holehe pyserial yt-dlp Pillow`
3. Hashcat → `C:\HashCat\hashcat-7.1.2\` + WSL с `hcxtools` (для конвертации pcap)
4. Скопировать `wifi_cracker/wifi_cracker.py` из репо в `C:\HashCat\hashcat-7.1.2\wifi_cracker.py`
5. Ввести API-ключи через вкладку **Settings** (создаст `keys.json` автоматически)
6. `python app.py` → открыть **http://localhost:7777**

---

## Структура проекта

`app.py` сам определяет свою папку, так что репозиторий можно клонировать куда угодно без правки путей.

```
osint-portrait\
    app.py              ← главный файл, точка входа (python app.py)
    index.html          ← оболочка хаба (шапка + вкладки)
    keys_store.py       ← читает/пишет keys.json
    vk_module.py        ← VK OSINT модуль
    frontend\           ← панель OSINT (отдаётся на /osint/)
    wifi_cracker\        ← панель WiFi Cracker (копия для версионирования)
```

Сам hashcat живёт отдельно от репозитория — в `C:\HashCat\hashcat-7.1.2\`, см. [Установку](#установка).

---

## Установка

### 1. Клонировать репо

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git -b kiki-hub C:\Users\<you>\osint-portrait
cd C:\Users\<you>\osint-portrait
```

Путь может быть любым — `app.py` сам определяет свою папку.

### 2. Python зависимости

```bash
pip install Flask flask-cors requests httpx beautifulsoup4 python-dotenv phonenumbers dnspython maigret holehe pyserial yt-dlp Pillow
```

Для AI-анализа в OSINT (опционально, можно установить только то, что планируешь использовать):

```bash
pip install google-genai anthropic openai
```

### 3. Hashcat

Скачать с [hashcat.net](https://hashcat.net/hashcat/) → распаковать в `C:\HashCat\hashcat-7.1.2\`

Проверить что GPU видится:
```bash
C:\HashCat\hashcat-7.1.2\hashcat.exe -I
```

Для NVIDIA — установить [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) (~3× быстрее чем OpenCL).

### 4. WSL + hcxpcapngtool

Нужен WSL для конвертации `.pcap` → `.hc22000`. **Важно:** `app.py` вызывает просто `wsl ...`
без `-d <дистрибутив>` — значит `hcxpcapngtool` должен быть установлен в **дефолтном** дистрибутиве
(том, что отмечен `*` в `wsl -l -v`). Конвертация идёт через `/tmp` внутри WSL (stdin/stdout),
поэтому `/mnt/c` дефолтного дистрибутива даже не используется — но если по умолчанию у тебя
стоит, например, `docker-desktop`, а `hcxtools` ты ставил в `Ubuntu`, конвертация будет молча
проваливаться (`hcxpcapngtool: command not found`) и EAPOL/PMKID всегда будет `0`.

```bash
# В PowerShell (от администратора)
wsl --install

# Проверить, какой дистрибутив дефолтный (со звёздочкой *)
wsl -l -v

# При необходимости сделать дефолтным:
wsl --set-default Ubuntu

# В WSL (в ДЕФОЛТНОМ дистрибутиве!)
sudo apt update && sudo apt install hcxtools -y

# Проверка — должно сработать БЕЗ -d
wsl hcxpcapngtool --version
```

### 5. Вордлисты

Положить `.txt` файлы в `C:\HashCat\hashcat-7.1.2\` — KikiHub подберёт все автоматически.

Рекомендованные:
- `rockyou.txt` — классика, 14M паролей
- `weakpass_4.latin.txt` — [weakpass.com](https://weakpass.com), 2B+ паролей (22 GB)

### 6. Запуск

```bash
python app.py
```

Открыть **http://localhost:7777**

---

## API ключи

Открой вкладку **Settings** в UI и вставь свои ключи — `keys.json` создастся автоматически
рядом с `app.py` (файл в `.gitignore`, остаётся только локально и никогда не попадает в git).

| Ключ | Где взять |
|------|-----------|
| VK Token | [vkhost.github.io](https://vkhost.github.io) → Kate Mobile |
| Gemini | [aistudio.google.com](https://aistudio.google.com) |
| Claude | [console.anthropic.com](https://console.anthropic.com) |
| ChatGPT | [platform.openai.com](https://platform.openai.com) |
| HaveIBeenPwned | [haveibeenpwned.com/API/Key](https://haveibeenpwned.com/API/Key) |

> 💡 **Gemini рекомендуется** для AI-анализа в OSINT — у него самый щедрый бесплатный лимит.

---

## Flipper Zero

- Подключить по USB, закрыть qFlipper (держит COM-порт)
- Выбрать порт в выпадашке (обычно COM4)
- Браузер показывает файловую систему SD-карты
- **↓** — скачать файл в браузер (для pcap проверяет EAPOL)
- **🔓** — отправить pcap напрямую в WiFi Cracker

---

## Известные проблемы

**`hcxpcapngtool: command not found`, EAPOL/PMKID всегда 0**
→ `hcxtools` установлен не в дефолтном дистрибутиве WSL. Проверь `wsl -l -v` и поставь нужный
дистрибутив дефолтным через `wsl --set-default <имя>` (см. [WSL + hcxpcapngtool](#установка)).

**Gemini API: "ошибка DNS" / `generativelanguage.googleapis.com` не резолвится**
→ Это известная проблема резолва DNS в некоторых сетях. KikiHub автоматически обходит её,
резолвя хост через DoH-сервис xbox-dns.ru и подключаясь по этому IP с правильным SNI. Если
ошибка сохраняется — проверь интернет-соединение.

**Gemini API: "User location is not supported"**
→ Геоблокировка Google по региону (например, для IP из РФ). KikiHub обходит и эту проблему
тем же способом, что и DNS-ошибку выше — резолвя Gemini через xbox-dns.ru на IP, не попадающий
под геоблок. Если ошибка всё равно появляется — перезапусти приложение (обход резолвится один
раз при старте и иногда может не сработать из-за нестабильного соединения).

**Flipper не виден / порт не открывается**
→ Закрой qFlipper и любые другие программы, держащие COM-порт. Проверь `pip install pyserial`.

---

## Disclaimer

Только для образовательных целей и авторизованного тестирования.

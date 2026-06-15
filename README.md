# KikiHub

> Локальный веб-хаб для OSINT, взлома WiFi хендшейков и управления файлами Flipper Zero — всё в одном интерфейсе.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows-0078d7?style=flat-square)


> [!WARNING]
> **LEGAL DISCLAIMER**
>
> This tool is provided for **educational purposes and authorized security testing only**.
>
> - ✅ Use only on networks and devices **you own** or have **explicit written permission** to test
> - ❌ Using this tool against networks without authorization is **illegal** in most jurisdictions (including Russia — Article 272-274 of the Criminal Code)
> - ❌ The author takes **no responsibility** for any misuse, damage, or legal consequences arising from use of this software
> - ❌ Capturing, cracking, or accessing Wi-Fi networks without consent is a criminal offence
>
> By using KikiHub you confirm that you have the legal right to test the target systems.

---

## Что нового в v2.0

- **Полная переносимость** — `app.py` сам определяет свою папку, репозиторий можно клонировать
  куда угодно без правки путей (раньше требовалось менять `OSINT_DIR`)
- **Панель OSINT и VK-модуль теперь в репозитории** — `frontend/` и `vk_module.py` версионируются,
  больше не нужна отдельная папка рядом
- **Свёртываемая нижняя панель** — шеврон над доком прячет/показывает панель вкладок
  (OSINT / WiFi Cracker / Flipper / Settings) с плавной анимацией, состояние сохраняется
- **Удобная кнопка закрытия результатов OSINT** — перенесена в левый верхний угол, не пересекается
  с другими элементами интерфейса
- **Gemini API** — обход локальных проблем с резолвом `generativelanguage.googleapis.com`
  через SNI-трюк, понятные русскоязычные сообщения об ошибках (DNS / геоблокировка региона)
- Метка **Gemini API (Recommended)** в настройках для бесплатного варианта по умолчанию

---

## Что внутри

| Вкладка | Описание |
|---------|----------|
| 🔍 **OSINT** | Username / email → цифровой портрет через VK, Maigret (500+ сайтов), HaveIBeenPwned, AI-анализ |
| 📶 **WiFi Cracker** | Бросаешь `.pcap` → конвертация → взлом hashcat. Мульти-SSID, терминал в реальном времени |
| 🐬 **Flipper Zero** | Браузер SD-карты по USB, скачивание `.pcap` прямо на ПК с проверкой EAPOL |

---

## Структура директорий

**Репозиторий теперь полностью переносимый** — `app.py` определяет свою папку автоматически
(`BASE_DIR = os.path.dirname(os.path.abspath(__file__))`), так что клонировать можно куда угодно,
никаких путей внутри репо менять не нужно.

Единственное, что жёстко прописано — путь к **hashcat** (константа `BASE`, app.py строка 42),
он живёт отдельно от репозитория:

```
<репо>\                            ← клонировать куда угодно
    app.py
    index.html                     ← оболочка хаба (шапка + 3 вкладки)
    keys_store.py                  ← читает keys.json (создаётся отдельно, в git не попадает)
    vk_module.py                   ← VK OSINT модуль
    flipper_logo.png
    kiki_logo.png
    hashcat_logo.png
    DISCLAIMER.md
    frontend\
        index.html                 ← панель OSINT (отдаётся на /osint/)
        kiki_logo.png
    wifi_cracker\
        wifi_cracker.py            ← копия для версионирования — ЖИВАЯ копия должна лежать
                                       в C:\HashCat\hashcat-7.1.2\wifi_cracker.py (см. ниже)
    temp\                          ← создаётся автоматически (временные файлы)

C:\HashCat\hashcat-7.1.2\         ← hashcat ИМЕННО здесь (константа BASE, app.py строка 42)
    hashcat.exe
    wifi_cracker.py                ← скопировать сюда из wifi_cracker\wifi_cracker.py репозитория —
                                       app.py читает embedded HTML панели именно из этого файла
                                       (строка 814) и запускает его как процесс (строка ~1068)
    hashes\                        ← .hc22000 / .pcap файлы (создаётся автоматически)
    rockyou.txt                    ← вордлисты сюда
    weakpass_4.latin.txt           ← и другие .txt
```

> Если путь до hashcat другой — поменяй `BASE` (строка 42), `wc_path` (строка ~814)
> и путь к `wifi_cracker.py` в `start_wificrack()` (строка ~1068) в `app.py`.

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
pip install Flask flask-cors pyserial
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

Создать `keys.json` рядом с `app.py` (в git не попадает):

```json
{
  "VK_TOKEN": "vk1.a.xxx",
  "GEMINI_API_KEY": "AIza...",
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "OPENAI_API_KEY": "sk-..."
}
```

Или прямо через вкладку **Settings** в UI — там же можно проверить что ключи сохранились.

| Ключ | Где взять |
|------|-----------|
| VK Token | [vkhost.github.io](https://vkhost.github.io) → Kate Mobile |
| Claude | [console.anthropic.com](https://console.anthropic.com) |
| ChatGPT | [platform.openai.com](https://platform.openai.com) |
| Gemini | [aistudio.google.com](https://aistudio.google.com) |

---

## Flipper Zero

- Подключить по USB, закрыть qFlipper (держит COM-порт)
- Выбрать порт в выпадашке (обычно COM4)
- Браузер показывает файловую систему SD-карты
- **↓** — скачать файл в браузер (для pcap проверяет EAPOL)
- **🔓** — отправить pcap напрямую в WiFi Cracker

---

## Disclaimer

Только для образовательных целей и авторизованного тестирования.



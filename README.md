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

## Что внутри

| Вкладка | Описание |
|---------|----------|
| 🔍 **OSINT** | Username / email → цифровой портрет через VK, Maigret (500+ сайтов), HaveIBeenPwned, AI-анализ |
| 📶 **WiFi Cracker** | Бросаешь `.pcap` → конвертация → взлом hashcat. Мульти-SSID, терминал в реальном времени |
| 🐬 **Flipper Zero** | Браузер SD-карты по USB, скачивание `.pcap` прямо на ПК с проверкой EAPOL |

---

## Структура директорий

**Это важно — пути жёстко прописаны в `app.py`:**

```
C:\KikiHub\                        ← сюда клонировать репо
    app.py
    index.html
    keys_store.py
    flipper_logo.png
    kiki_logo.png
    hashcat_logo.png
    temp\                          ← создаётся автоматически (временные файлы)

C:\HashCat\hashcat-7.1.2\         ← hashcat ИМЕННО здесь
    hashcat.exe
    hashes\                        ← .hc22000 файлы (создаётся автоматически)
    rockyou.txt                    ← вордлисты сюда
    weakpass_4.latin.txt           ← и другие .txt
```

> Если хочешь другие пути — измени `BASE_DIR`, `HC_DIR`, `DOWNLOADS_DIR` в начале `app.py`.

---

## Установка

### 1. Клонировать репо

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint.git -b kiki-hub C:\KikiHub
cd C:\KikiHub
```

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

Нужен WSL (Ubuntu) для конвертации `.pcap` → `.hc22000`:

```bash
# В PowerShell (от администратора)
wsl --install

# В WSL
sudo apt update && sudo apt install hcxtools -y

# Проверка
hcxpcapngtool --version
```

### 5. Вордлисты

Положить `.txt` файлы в `C:\HashCat\hashcat-7.1.2\` — KikiHub подберёт все автоматически.

Рекомендованные:
- `rockyou.txt` — классика, 14M паролей
- `weakpass_4.latin.txt` — [weakpass.com](https://weakpass.com), 2B+ паролей (22 GB)

### 6. Запуск

```bash
python C:\KikiHub\app.py
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

## Workflow: взлом WiFi

```
1. Захват: Flipper Zero + Marauder → sniffpmkid.pcap / sniffpmkid_X.pcap
2. Вкладка Flipper → выбрать COM-порт → найти файл → нажать ↓
3. Файл скачивается в браузер, EAPOL фреймы подсчитываются автоматически
   └─ Оранжевый = меньше 2 фреймов (хендшейк неполный)
   └─ Зелёный = готово к взлому
4. Нажать 🔓 → файл уходит в WiFi Cracker
5. Вкладка WiFi Cracker → Analyze → Run Hashcat
6. Пароль появится в терминале когда найден
```

> Скорость на RTX 3050: ~117 kH/s (OpenCL) / ~400 kH/s (CUDA)

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


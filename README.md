# Kiki OSINT

Open-source intelligence tool for building a digital portrait of a person by username, email, or phone number.

## Features

- **Username search** — Maigret (400+ sites) + Holehe (email check) + VK + Telegram + GitHub
- **Email lookup** — Gravatar, GitHub email, HIBP breach check
- **Phone lookup** — carrier, region, validation via phonenumbers
- **AI portrait** — analyzes found data and generates a short personality/risk summary
- **Connection analysis** — highlights cards from different sources that share the same name or location
- Pixel card animations with per-source color coding
- Live log stream with source-colored tags

## Stack

- Backend: Python / Flask (SSE streaming)
- Frontend: Vanilla JS + HTML/CSS (no build step)
- AI: Gemini 2.0 Flash / GPT-4o mini / Claude Sonnet (configurable)

## Installation

```bash
git clone https://github.com/kikikoteyka-dev/Kiki-Osint
cd Kiki-Osint
pip install -r requirements.txt
```

Copy `keys.json.example` → `keys.json` and fill in your API keys (or use the Settings UI in the app).

```bash
py app.py
# Open http://localhost:5000
```

## API Keys

| Key | Where to get |
|-----|--------------|
| `VK_TOKEN` | [vk.com/apps](https://vk.com/apps) — Standalone app → Access Token |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `HIBP_API_KEY` | [haveibeenpwned.com/API/Key](https://haveibeenpwned.com/API/Key) |

Keys are stored locally in `keys.json` (git-ignored) and can be updated anytime via **Settings** in the app.

## Gemini in Russia (xbox-dns.ru)

Google AI (`generativelanguage.googleapis.com`) is blocked in some regions including Russia.
The app automatically works around this using **[xbox-dns.ru](https://xbox-dns.ru)** — a free DNS service that routes around regional restrictions.

**How it works:**
At startup, `app.py` resolves `generativelanguage.googleapis.com` through the xbox-dns.ru nameservers (`176.99.11.77` / `80.78.247.254`) instead of the system DNS. The resolved IP is cached, and only Gemini API calls use it — all other traffic goes through your normal DNS unchanged.

**Requirements:**
- `dnspython` must be installed (`pip install dnspython` — already in `requirements.txt`)
- No system DNS changes needed, no VPN required
- If the DNS lookup fails (e.g. xbox-dns.ru is unreachable), the app falls back to your system DNS automatically

If Gemini still fails with `FAILED_PRECONDITION: User location is not supported`, try:
1. Check that `dnspython` is installed: `python -c "import dns.resolver"`
2. Check the startup log for `[Gemini DNS] generativelanguage.googleapis.com → <IP>`
3. As a last resort, set your system DNS to `176.99.11.77` manually

## Notes

- Maigret and Holehe run as subprocesses and must be installed in the same (or a discoverable) Python environment
- Reports are saved to `reports/` (git-ignored)
- The app uses `pythonw.exe` on Windows to avoid CMD windows popping up during search

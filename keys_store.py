"""Shared key store — reads from keys.json, falls back to .env"""
import os, sys, json
from dotenv import load_dotenv

# When frozen by PyInstaller, __file__ resolves inside the temp extraction
# dir (sys._MEIPASS), which is wiped on every launch — keys.json must live
# next to the actual .exe instead, so saved tokens persist across restarts.
_APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(_APP_DIR, "keys.json")

def load():
    try:
        with open(KEYS_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        load_dotenv()
        return {
            "VK_TOKEN":          os.getenv("VK_TOKEN", ""),
            "GEMINI_API_KEY":    os.getenv("GEMINI_API_KEY", ""),
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            "MISTRAL_API_KEY":   os.getenv("MISTRAL_API_KEY", ""),
            "HIBP_API_KEY":      os.getenv("HIBP_API_KEY", ""),
        }

def get(name: str) -> str:
    return load().get(name) or ""

def save(data: dict) -> None:
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

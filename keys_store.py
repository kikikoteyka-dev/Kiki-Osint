"""Shared key store — reads from keys.json, falls back to .env"""
import os, json
from dotenv import load_dotenv

KEYS_FILE = os.path.join(os.path.dirname(__file__), "keys.json")

def load():
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        load_dotenv()
        return {
            "VK_TOKEN":          os.getenv("VK_TOKEN", ""),
            "GEMINI_API_KEY":    os.getenv("GEMINI_API_KEY", ""),
            "OPENAI_API_KEY":    os.getenv("OPENAI_API_KEY", ""),
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            "HIBP_API_KEY":      os.getenv("HIBP_API_KEY", ""),
        }

def get(name: str) -> str:
    return load().get(name) or ""

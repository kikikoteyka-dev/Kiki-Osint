import asyncio
from telethon import TelegramClient
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.contacts import ResolveUsernameRequest
from config import TG_API_ID, TG_API_HASH

async def get_tg_profile(username: str) -> dict:
    """Получает публичный профиль Telegram пользователя"""
    username = username.lstrip("@").split("/")[-1]
    client = TelegramClient("osint_session", TG_API_ID, TG_API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {"error": "Нужна авторизация Telegram — запусти auth.py"}

        result = await client(ResolveUsernameRequest(username))
        user = result.users[0] if result.users else None
        if not user:
            return {"error": "Пользователь не найден"}

        full = await client(GetFullUserRequest(user.id))
        full_user = full.full_user

        return {
            "source": "Telegram",
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username": user.username,
            "id": user.id,
            "bio": getattr(full_user, "about", "") or "",
            "phone": getattr(user, "phone", "") or "",
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        await client.disconnect()

def get_tg_profile_sync(username: str) -> dict:
    return asyncio.run(get_tg_profile(username))

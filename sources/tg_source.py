import asyncio
from telethon import TelegramClient
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.contacts import ResolveUsernameRequest

async def get_tg_info(api_id: int, api_hash: str, username: str) -> dict:
    client = TelegramClient("osint_session", api_id, api_hash)
    try:
        await client.start()
        username = username.lstrip("@")

        resolved = await client(ResolveUsernameRequest(username))
        user = resolved.users[0] if resolved.users else None

        if not user:
            return {"error": "Пользователь не найден"}

        full = await client(GetFullUserRequest(user.id))

        # Ищем сообщения в публичных чатах (где юзер писал)
        messages = []
        try:
            async for dialog in client.iter_dialogs():
                if dialog.is_channel or dialog.is_group:
                    try:
                        async for msg in client.iter_messages(dialog.id, from_user=user.id, limit=5):
                            if msg.text:
                                messages.append({
                                    "chat": dialog.name,
                                    "text": msg.text[:200]
                                })
                        if len(messages) >= 20:
                            break
                    except:
                        continue
        except:
            pass

        return {
            "source": "telegram",
            "id": user.id,
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username": user.username or "",
            "phone": user.phone or "",
            "bio": full.full_user.about or "",
            "profile_url": f"https://t.me/{user.username}" if user.username else "",
            "messages": messages
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        await client.disconnect()

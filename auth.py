import asyncio
from telethon import TelegramClient
from config import TG_API_ID, TG_API_HASH

async def authorize():
    client = TelegramClient("osint_session", TG_API_ID, TG_API_HASH)
    await client.start()
    print("Авторизация успешна!")
    await client.disconnect()

asyncio.run(authorize())

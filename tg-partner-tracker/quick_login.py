#!/usr/bin/env python3
"""
Quick login using opentele's built-in API credentials.
No need to visit my.telegram.org.
"""
import asyncio
from pathlib import Path

from telethon import TelegramClient

SESSION = str(Path(__file__).resolve().parent / "tg_session")

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"


async def main():
    api_id = API_ID
    api_hash = API_HASH

    print(f"Using api_id={api_id}")
    print("Will prompt for phone number and TG verification code.\n")

    client = TelegramClient(SESSION, api_id, api_hash)
    await client.start()

    me = await client.get_me()
    print(f"\n✅ 登录成功: {me.first_name} (@{me.username}) ID={me.id}")
    print(f"   api_id={api_id}")
    print(f"   session saved to tg_session.session")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

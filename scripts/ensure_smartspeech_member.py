#!/usr/bin/env python3
"""Add SaluteSpeech to the locally selected Telegram group and verify membership."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, errors, types
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def ensure_member() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    target_id = int(os.environ["TARGET_CHAT_ID"])
    session_path = Path(os.environ["TELEGRAM_SESSION_PATH"])
    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telethon session is not authorized")
        group = await client.get_entity(target_id)
        bot = await client.get_entity("smartspeech_sber_bot")
        present = False
        async for participant in client.iter_participants(group):
            if participant.id == bot.id:
                present = True
                break
        if not present:
            try:
                if isinstance(group, types.Channel) and bool(getattr(group, "megagroup", False)):
                    await client(InviteToChannelRequest(group, [bot]))
                elif isinstance(group, types.Chat):
                    await client(AddChatUserRequest(group.id, bot, fwd_limit=0))
                else:
                    raise RuntimeError("selected target is not a Telegram group")
            except errors.UserAlreadyParticipantError:
                pass

        verified = False
        async for participant in client.iter_participants(group):
            if participant.id == bot.id:
                verified = True
                break
        if not verified:
            raise RuntimeError("SaluteSpeech membership could not be verified")
        print(f"TARGET_GROUP_TITLE={getattr(group, 'title', '')}")
        print(f"TARGET_GROUP_ID={target_id}")
        print("SALUTESPEECH_MEMBER=1")
    finally:
        await client.disconnect()
        if session_path.exists():
            os.chmod(session_path, 0o600)


if __name__ == "__main__":
    try:
        asyncio.run(ensure_member())
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}")
        raise SystemExit(1)

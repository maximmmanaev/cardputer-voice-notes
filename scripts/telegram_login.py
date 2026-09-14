#!/usr/bin/env python3
"""Create a local Telethon user session and select a Telegram group."""

from __future__ import annotations

import asyncio
import getpass
import json
import os
from pathlib import Path

from dotenv import dotenv_values, set_key
from telethon import TelegramClient, errors, utils


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_SESSION = PROJECT_ROOT / "data/telegram/cardputer-recorder.session"
TARGET_STATE = PROJECT_ROOT / "data/telegram/selected-target.json"


def secret_value(config: dict[str, str | None], name: str, prompt: str) -> str:
    current = config.get(name)
    if current:
        return current
    value = getpass.getpass(prompt).strip()
    if not value:
        raise ValueError(f"{name} is required")
    set_key(ENV_PATH, name, value)
    os.chmod(ENV_PATH, 0o600)
    config[name] = value
    return value


async def login_and_select() -> None:
    ENV_PATH.touch(mode=0o600, exist_ok=True)
    os.chmod(ENV_PATH, 0o600)
    config = dict(dotenv_values(ENV_PATH))
    api_id_text = secret_value(config, "TELEGRAM_API_ID", "Telegram api_id (hidden): ")
    api_hash = secret_value(config, "TELEGRAM_API_HASH", "Telegram api_hash (hidden): ")
    phone = secret_value(config, "TELEGRAM_PHONE", "Telegram phone in international format (hidden): ")
    try:
        api_id = int(api_id_text)
    except ValueError as error:
        raise ValueError("TELEGRAM_API_ID must be numeric") from error

    session_path = Path(config.get("TELEGRAM_SESSION_PATH") or DEFAULT_SESSION)
    if not session_path.is_absolute():
        session_path = PROJECT_ROOT / session_path
    session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    set_key(ENV_PATH, "TELEGRAM_SESSION_PATH", str(session_path))

    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            sent = await client.send_code_request(phone)
            code = getpass.getpass("One-time Telegram code (hidden): ").strip()
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            except errors.SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password (hidden): ")
                await client.sign_in(password=password)

        me = await client.get_me()
        print(f"Authorized Telegram user: {utils.get_display_name(me)}")
        groups = [dialog async for dialog in client.iter_dialogs() if dialog.is_group]
        if not groups:
            raise RuntimeError("No Telegram groups are visible to this account")
        print("\nAvailable groups:")
        for index, dialog in enumerate(groups, start=1):
            print(f"  {index}. {dialog.title}")
        selection = int(input("\nSelect target group number: ").strip())
        if selection < 1 or selection > len(groups):
            raise ValueError("group selection is out of range")
        target = groups[selection - 1]
        target_id = utils.get_peer_id(target.entity)
        set_key(ENV_PATH, "TARGET_CHAT_ID", str(target_id))
        os.chmod(ENV_PATH, 0o600)

        bot_present = False
        try:
            async for participant in client.iter_participants(target.entity):
                if (getattr(participant, "username", "") or "").lower() == "smartspeech_sber_bot":
                    bot_present = True
                    break
        except Exception:
            pass
        TARGET_STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        TARGET_STATE.write_text(
            json.dumps({"title": target.title, "id": target_id, "smartspeech_present": bot_present}),
            encoding="utf-8",
        )
        os.chmod(TARGET_STATE, 0o600)
        print(f"\nSelected group: {target.title}")
        print("SaluteSpeech member found." if bot_present else "SaluteSpeech member was not found; add @smartspeech_sber_bot before sending.")
        print("No message has been sent. Return to Codex for target confirmation.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(login_and_select())
    except Exception as error:
        print(f"Setup failed: {error}")
        raise SystemExit(1)

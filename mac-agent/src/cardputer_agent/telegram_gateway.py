from __future__ import annotations

import os
from pathlib import Path

from telethon import TelegramClient, types

from .config import Settings


class TelethonGateway:
    def __init__(self, settings: Settings):
        settings.validate(require_telegram=True)
        self.settings = settings

    def _client(self) -> TelegramClient:
        assert self.settings.telegram_api_id is not None
        assert self.settings.telegram_api_hash is not None
        assert self.settings.telegram_session_path is not None
        return TelegramClient(
            str(self.settings.telegram_session_path),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
            receive_updates=False,
        )

    async def _connect(self) -> tuple[TelegramClient, object]:
        client = self._client()
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError("Telethon user session is not authorized")
        entity = await client.get_entity(self.settings.target_chat_id)
        is_group = isinstance(entity, types.Chat) or (
            isinstance(entity, types.Channel) and bool(getattr(entity, "megagroup", False))
        )
        if not is_group:
            await client.disconnect()
            raise RuntimeError("TARGET_CHAT_ID is not a Telegram group")
        return client, entity

    @staticmethod
    def _filename(message: object) -> str | None:
        document = getattr(message, "document", None)
        if document is None:
            return None
        for attribute in getattr(document, "attributes", []):
            if isinstance(attribute, types.DocumentAttributeFilename):
                return attribute.file_name
        return None

    async def find_existing(self, recording_id: str) -> int | None:
        client, entity = await self._connect()
        try:
            expected = f"{recording_id}.ogg"
            async for message in client.iter_messages(entity, limit=2000, from_user="me"):
                if self._filename(message) == expected:
                    return message.id
            return None
        finally:
            await client.disconnect()
            session = self.settings.telegram_session_path
            if session and session.exists():
                os.chmod(session, 0o600)

    async def send_voice(self, recording_id: str, path: Path) -> int:
        client, entity = await self._connect()
        try:
            message = await client.send_file(entity, str(path), voice_note=True)
            return message.id
        finally:
            await client.disconnect()
            session = self.settings.telegram_session_path
            if session and session.exists():
                os.chmod(session, 0o600)

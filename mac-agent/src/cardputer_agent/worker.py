from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .config import Settings
from .media import MediaError, convert_and_validate
from .repository import Repository


class VoiceGateway(Protocol):
    async def find_existing(self, recording_id: str) -> int | None: ...

    async def send_voice(self, recording_id: str, path: Path) -> int: ...


class QueueWorker:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        *,
        gateway: VoiceGateway | None = None,
        converter: Callable[..., object] = convert_and_validate,
    ):
        self.settings = settings
        self.repository = repository
        if gateway is None:
            from .telegram_gateway import TelethonGateway

            gateway = TelethonGateway(settings)
        self.gateway = gateway
        self.converter = converter

    @staticmethod
    def _move(source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if source != destination and source.exists():
            os.replace(source, destination)
        return destination

    async def process_one(self) -> bool:
        record = self.repository.next_pending()
        if record is None:
            return False
        wav_path = Path(record.wav_path or self.settings.ready_dir / f"{record.recording_id}.wav")
        ogg_path = Path(record.ogg_path or self.settings.ready_dir / f"{record.recording_id}.ogg")
        try:
            if record.state in ("ready", "failed") or not ogg_path.exists():
                self.repository.set_state(record.recording_id, "converting")
                self.converter(
                    wav_path,
                    ogg_path,
                    ffmpeg=self.settings.ffmpeg_path,
                    ffprobe=self.settings.ffprobe_path,
                )
                self.repository.set_state(record.recording_id, "converted", ogg_path=ogg_path)

            self.repository.set_state(record.recording_id, "sending", increment_attempts=True)
            message_id = await self.gateway.find_existing(record.recording_id)
            if message_id is None:
                message_id = await self.gateway.send_voice(record.recording_id, ogg_path)

            # Persist the Telegram identity before final filesystem bookkeeping.
            self.repository.mark_message_confirmed(record.recording_id, message_id)
            sent_wav = self._move(wav_path, self.settings.sent_dir / wav_path.name)
            sent_ogg = self._move(ogg_path, self.settings.sent_dir / ogg_path.name)
            self.repository.mark_sent(record.recording_id, message_id, sent_wav, sent_ogg)
            return True
        except MediaError as error:
            self.repository.set_state(record.recording_id, "failed", error=str(error), increment_attempts=True)
            return True
        except Exception as error:
            # Telegram/network errors are retryable. Keep strict chronological order.
            fallback = "converted" if ogg_path.exists() else "ready"
            self.repository.set_state(record.recording_id, fallback, error=type(error).__name__)
            raise


async def run_worker_loop(settings: Settings, repository: Repository) -> None:
    settings.validate(require_telegram=True)
    worker = QueueWorker(settings, repository)
    failure_count = 0
    while True:
        try:
            processed = await worker.process_one()
            failure_count = 0
            await asyncio.sleep(0.25 if processed else 2)
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_count += 1
            await asyncio.sleep(min(300, 2 ** min(failure_count, 8)))

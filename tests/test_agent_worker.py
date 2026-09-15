from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mac-agent/src"))

from cardputer_agent.config import Settings
from cardputer_agent.repository import Repository
from cardputer_agent.worker import QueueWorker


class FakeGateway:
    def __init__(self, existing: dict[str, int] | None = None):
        self.existing = existing or {}
        self.sent: list[str] = []

    async def find_existing(self, recording_id: str) -> int | None:
        return self.existing.get(recording_id)

    async def send_voice(self, recording_id: str, path: Path) -> int:
        self.sent.append(recording_id)
        message_id = 100 + len(self.sent)
        self.existing[recording_id] = message_id
        return message_id


def fake_convert(source: Path, destination: Path, **_: object) -> object:
    destination.write_bytes(b"OggS-opus")
    return object()


class AgentWorkerTests(unittest.TestCase):
    def make_repository(self, root: Path) -> tuple[Settings, Repository]:
        settings = Settings.for_tests(root, device_token="test-device-token")
        repository = Repository(settings)
        repository.initialize()
        return settings, repository

    def add_ready(self, settings: Settings, repository: Repository, recording_id: str) -> None:
        wav = settings.ready_dir / f"{recording_id}.wav"
        wav.write_bytes(b"RIFF" + b"x" * 64)
        repository.register_or_validate(
            recording_id,
            filename=wav.name,
            expected_size=wav.stat().st_size,
            sha256="0" * 64,
            duration_ms=1000,
        )
        repository.mark_ready(recording_id, wav)

    def test_sends_once_and_persists_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, repository = self.make_repository(Path(directory))
            self.add_ready(settings, repository, "device-s001-f001")
            gateway = FakeGateway()
            worker = QueueWorker(settings, repository, gateway=gateway, converter=fake_convert)
            self.assertTrue(asyncio.run(worker.process_one()))
            record = repository.get("device-s001-f001")
            self.assertEqual(record.state, "sent")
            self.assertEqual(record.telegram_message_id, 101)
            self.assertEqual(gateway.sent, ["device-s001-f001"])
            self.assertFalse(asyncio.run(worker.process_one()))

    def test_reconciles_telegram_message_after_crash_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, repository = self.make_repository(Path(directory))
            self.add_ready(settings, repository, "device-s001-f002")
            repository.set_state("device-s001-f002", "sending", increment_attempts=True)
            repository.recover_inflight()
            gateway = FakeGateway(existing={"device-s001-f002": 777})
            worker = QueueWorker(settings, repository, gateway=gateway, converter=fake_convert)
            self.assertTrue(asyncio.run(worker.process_one()))
            record = repository.get("device-s001-f002")
            self.assertEqual(record.telegram_message_id, 777)
            self.assertEqual(gateway.sent, [])


if __name__ == "__main__":
    unittest.main()

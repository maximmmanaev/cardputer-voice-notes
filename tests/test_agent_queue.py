from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mac-agent/src"))

from cardputer_agent.config import Settings
from cardputer_agent.repository import Repository


class AgentQueueTests(unittest.TestCase):
    def test_recovery_and_queue_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.for_tests(Path(directory), device_token="token")
            repository = Repository(settings)
            repository.initialize()
            for recording_id in ("device-s01-f003", "device-s01-f001", "device-s01-f002"):
                repository.register_complete_for_test(recording_id, state="ready")
            repository.set_state("device-s01-f001", "converting")
            repository.set_state("device-s01-f002", "sending")

            repository.recover_inflight()

            self.assertEqual(repository.get("device-s01-f001").state, "ready")
            self.assertEqual(repository.get("device-s01-f002").state, "ready")
            self.assertEqual(repository.next_pending().recording_id, "device-s01-f001")

    def test_unsynced_recordings_are_never_retention_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.for_tests(Path(directory), device_token="token")
            repository = Repository(settings)
            repository.initialize()
            repository.register_complete_for_test("pending", state="ready")
            repository.register_complete_for_test("delivered", state="sent")
            self.assertEqual(repository.retention_candidates(), ["delivered"])

    def test_failed_recording_waits_for_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.for_tests(Path(directory), device_token="token")
            repository = Repository(settings)
            repository.initialize()
            repository.register_complete_for_test("failed", state="failed")
            self.assertIsNone(repository.next_pending())
            self.assertEqual(repository.retry_failed(), 1)
            self.assertEqual(repository.next_pending().recording_id, "failed")


if __name__ == "__main__":
    unittest.main()

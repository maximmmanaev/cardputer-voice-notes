from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mac-agent/src"))

from cardputer_agent.app import create_app
from cardputer_agent.config import Settings


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings.for_tests(self.root, device_token="test-device-token")
        self.client_context = TestClient(create_app(self.settings, run_worker=False))
        self.client = self.client_context.__enter__()
        self.auth = {"Authorization": "Bearer test-device-token"}

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    def upload(self, recording_id: str, payload: bytes, offset: int, total: bytes):
        headers = {
            **self.auth,
            "X-Upload-Offset": str(offset),
            "X-Recording-Size": str(len(total)),
            "X-Recording-SHA256": hashlib.sha256(total).hexdigest(),
            "X-Recording-Filename": f"{recording_id}.wav",
            "Content-Type": "application/octet-stream",
        }
        return self.client.put(f"/v1/recordings/{recording_id}", headers=headers, content=payload)

    def test_resumes_uploads_interrupted_at_30_70_and_99_percent(self) -> None:
        audio = b"RIFF" + bytes(range(256)) * 400
        for percent in (30, 70, 99):
            recording_id = f"resume-{percent}"
            split = len(audio) * percent // 100
            first = self.upload(recording_id, audio[:split], 0, audio)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["offset"], split)
            self.assertFalse(first.json()["durable_ack"])

            status = self.client.get(f"/v1/recordings/{recording_id}", headers=self.auth)
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["offset"], split)

            second = self.upload(recording_id, audio[split:], split, audio)
            self.assertEqual(second.status_code, 200)
            self.assertTrue(second.json()["durable_ack"])
            ready = self.root / "ready" / f"{recording_id}.wav"
            self.assertEqual(ready.read_bytes(), audio)

    def test_repeat_after_durable_ack_is_idempotent(self) -> None:
        audio = b"RIFF" + b"i" * 64
        first = self.upload("same-recording", audio, 0, audio)
        self.assertTrue(first.json()["durable_ack"])
        repeat = self.upload("same-recording", audio, 0, audio)
        self.assertEqual(repeat.status_code, 200)
        self.assertTrue(repeat.json()["already_accepted"])
        self.assertEqual(list((self.root / "ready").glob("*.wav")), [self.root / "ready/same-recording.wav"])

    def test_wrong_token_is_rejected_without_creating_file(self) -> None:
        response = self.client.put(
            "/v1/recordings/rejected",
            headers={"Authorization": "Bearer wrong"},
            content=b"audio",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(list((self.root / "incoming").iterdir()), [])

    def test_wrong_resume_offset_returns_expected_offset(self) -> None:
        audio = b"RIFF" + b"o" * 64
        self.upload("offset", audio[:5], 0, audio)
        response = self.upload("offset", audio[5:], 3, audio)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["expected_offset"], 5)

    def test_path_traversal_recording_id_is_rejected(self) -> None:
        response = self.client.put(
            "/v1/recordings/bad..id",
            headers={**self.auth, "X-Upload-Offset": "0", "X-Recording-Size": "4", "X-Recording-SHA256": "0" * 64, "X-Recording-Filename": "bad.wav"},
            content=b"RIFF",
        )
        self.assertEqual(response.status_code, 422)

    def test_database_contains_one_row_after_idempotent_repeat(self) -> None:
        audio = b"RIFF" + b"d" * 64
        self.upload("db-one", audio, 0, audio)
        self.upload("db-one", audio, 0, audio)
        with closing(sqlite3.connect(self.root / "agent.sqlite3")) as connection:
            count = connection.execute("SELECT COUNT(*) FROM recordings WHERE recording_id = ?", ("db-one",)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_status_repairs_stale_offset_from_partial_file(self) -> None:
        audio = b"RIFF" + b"r" * 64
        self.upload("cancelled", audio[:16], 0, audio)
        part = self.root / "incoming" / "cancelled.wav.part"
        with part.open("ab") as output:
            output.write(audio[16:31])
            output.flush()
        status = self.client.get("/v1/recordings/cancelled", headers=self.auth)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["offset"], 31)
        self.assertFalse(status.json()["durable_ack"])

    def test_failed_hash_is_never_reported_as_durable(self) -> None:
        audio = b"RIFF" + b"x" * 64
        headers = {
            **self.auth,
            "X-Upload-Offset": "0",
            "X-Recording-Size": str(len(audio)),
            "X-Recording-SHA256": "0" * 64,
            "X-Recording-Filename": "bad-hash.wav",
        }
        response = self.client.put("/v1/recordings/bad-hash", headers=headers, content=audio)
        self.assertEqual(response.status_code, 422)
        status = self.client.get("/v1/recordings/bad-hash", headers=self.auth)
        self.assertFalse(status.json()["durable_ack"])
        self.assertEqual(status.json()["state"], "failed")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Iterator

from .config import Settings


ACTIVE_STATES = ("ready", "converting", "converted", "sending", "failed")


@dataclass(frozen=True)
class Recording:
    recording_id: str
    filename: str
    expected_size: int
    sha256: str
    received_size: int
    state: str
    attempts: int
    last_error: str | None
    telegram_message_id: int | None
    duration_ms: int | None
    wav_path: str | None
    ogg_path: str | None
    created_at: str
    updated_at: str


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> Recording | None:
        return Recording(**dict(row)) if row is not None else None

    def initialize(self) -> None:
        self.settings.create_directories()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recordings (
                    recording_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    expected_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    received_size INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    telegram_message_id INTEGER,
                    duration_ms INTEGER,
                    wav_path TEXT,
                    ogg_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS recordings_queue ON recordings(state, recording_id)")

    def get(self, recording_id: str) -> Recording | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM recordings WHERE recording_id = ?", (recording_id,)).fetchone()
        return self._record(row)

    def register_or_validate(
        self,
        recording_id: str,
        *,
        filename: str,
        expected_size: int,
        sha256: str,
        duration_ms: int | None,
    ) -> Recording:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO recordings
                (recording_id, filename, expected_size, sha256, received_size, state, duration_ms, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, 'receiving', ?, ?, ?)
                """,
                (recording_id, filename, expected_size, sha256, duration_ms, now, now),
            )
            row = connection.execute("SELECT * FROM recordings WHERE recording_id = ?", (recording_id,)).fetchone()
        assert row is not None
        record = self._record(row)
        assert record is not None
        if record.expected_size != expected_size or record.sha256 != sha256:
            raise ValueError("recording_id already exists with different immutable metadata")
        return record

    def update_received(self, recording_id: str, received_size: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE recordings SET received_size = ?, updated_at = ? WHERE recording_id = ?",
                (received_size, self._now(), recording_id),
            )

    def mark_ready(self, recording_id: str, wav_path: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE recordings SET received_size = expected_size, state = 'ready', wav_path = ?, last_error = NULL, updated_at = ? WHERE recording_id = ?",
                (str(wav_path), self._now(), recording_id),
            )

    def set_state(
        self,
        recording_id: str,
        state: str,
        *,
        error: str | None = None,
        ogg_path: Path | None = None,
        telegram_message_id: int | None = None,
        increment_attempts: bool = False,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE recordings SET state = ?, last_error = ?,
                    ogg_path = COALESCE(?, ogg_path),
                    telegram_message_id = COALESCE(?, telegram_message_id),
                    attempts = attempts + ?, updated_at = ?
                WHERE recording_id = ?
                """,
                (
                    state,
                    error,
                    str(ogg_path) if ogg_path else None,
                    telegram_message_id,
                    1 if increment_attempts else 0,
                    self._now(),
                    recording_id,
                ),
            )

    def next_pending(self) -> Recording | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM recordings
                WHERE state IN ('ready', 'converted') AND telegram_message_id IS NULL
                ORDER BY recording_id ASC LIMIT 1
                """
            ).fetchone()
        return self._record(row)

    def list_by_states(self, states: tuple[str, ...] = ACTIVE_STATES) -> list[Recording]:
        placeholders = ",".join("?" for _ in states)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM recordings WHERE state IN ({placeholders}) ORDER BY recording_id", states
            ).fetchall()
        return [self._record(row) for row in rows if row is not None]

    def recover_inflight(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE recordings SET state = 'ready', last_error = NULL, updated_at = ? WHERE state IN ('converting', 'sending') AND telegram_message_id IS NULL",
                (self._now(),),
            )
            connection.execute(
                "UPDATE recordings SET state = 'sent', last_error = NULL, updated_at = ? WHERE telegram_message_id IS NOT NULL AND state != 'sent'",
                (self._now(),),
            )

    def retention_candidates(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT recording_id FROM recordings WHERE state = 'sent' AND telegram_message_id IS NOT NULL ORDER BY recording_id"
            ).fetchall()
        return [row[0] for row in rows]

    def mark_message_confirmed(self, recording_id: str, message_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE recordings SET telegram_message_id = ?, state = 'sending', last_error = NULL, updated_at = ? WHERE recording_id = ?",
                (message_id, self._now(), recording_id),
            )

    def mark_sent(self, recording_id: str, message_id: int, wav_path: Path, ogg_path: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE recordings SET state = 'sent', telegram_message_id = ?, wav_path = ?, ogg_path = ?,
                    last_error = NULL, updated_at = ? WHERE recording_id = ?
                """,
                (message_id, str(wav_path), str(ogg_path), self._now(), recording_id),
            )

    def retry_failed(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE recordings SET state = 'ready', last_error = NULL, updated_at = ? WHERE state = 'failed'",
                (self._now(),),
            )
            return cursor.rowcount

    def register_complete_for_test(self, recording_id: str, *, state: str) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO recordings
                (recording_id, filename, expected_size, sha256, received_size, state, created_at, updated_at)
                VALUES (?, ?, 4, ?, 4, ?, ?, ?)
                """,
                (recording_id, f"{recording_id}.wav", "0" * 64, state, now, now),
            )
            if state == "sent":
                connection.execute(
                    "UPDATE recordings SET telegram_message_id = 1 WHERE recording_id = ?",
                    (recording_id,),
                )

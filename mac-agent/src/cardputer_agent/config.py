from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_root: Path
    device_token: str = field(repr=False)
    listen_host: str = "0.0.0.0"
    listen_port: int = 8765
    telegram_api_id: int | None = field(default=None, repr=False)
    telegram_api_hash: str | None = field(default=None, repr=False)
    target_chat_id: int | None = None
    telegram_session_path: Path | None = field(default=None, repr=False)
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    max_recording_bytes: int = 64 * 1024 * 1024
    telegram_reply_timeout: int = 120

    @property
    def database_path(self) -> Path:
        return self.data_root / "agent.sqlite3"

    @property
    def incoming_dir(self) -> Path:
        return self.data_root / "incoming"

    @property
    def ready_dir(self) -> Path:
        return self.data_root / "ready"

    @property
    def sent_dir(self) -> Path:
        return self.data_root / "sent"

    @property
    def failed_dir(self) -> Path:
        return self.data_root / "failed"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    def create_directories(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_root, 0o700)
        for directory in (self.incoming_dir, self.ready_dir, self.sent_dir, self.failed_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)

    def validate(self, *, require_telegram: bool = False) -> None:
        if len(self.device_token) < 16:
            raise ValueError("DEVICE_TOKEN must contain at least 16 characters")
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("LISTEN_PORT must be between 1 and 65535")
        if require_telegram:
            missing = []
            if not self.telegram_api_id:
                missing.append("TELEGRAM_API_ID")
            if not self.telegram_api_hash:
                missing.append("TELEGRAM_API_HASH")
            if not self.target_chat_id:
                missing.append("TARGET_CHAT_ID")
            if not self.telegram_session_path:
                missing.append("TELEGRAM_SESSION_PATH")
            if missing:
                raise ValueError(f"missing Telegram configuration: {', '.join(missing)}")

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "Settings":
        if env_path is not None:
            load_dotenv(env_path)
        else:
            load_dotenv()
        root = Path(os.getenv("DATA_ROOT", "data/agent")).expanduser().resolve()
        api_id = int(os.environ["TELEGRAM_API_ID"]) if os.getenv("TELEGRAM_API_ID") else None
        target_id = int(os.environ["TARGET_CHAT_ID"]) if os.getenv("TARGET_CHAT_ID") else None
        session = Path(os.environ["TELEGRAM_SESSION_PATH"]).expanduser().resolve() if os.getenv("TELEGRAM_SESSION_PATH") else None
        return cls(
            data_root=root,
            device_token=os.getenv("DEVICE_TOKEN", ""),
            listen_host=os.getenv("LISTEN_HOST", "0.0.0.0"),
            listen_port=int(os.getenv("LISTEN_PORT", "8765")),
            telegram_api_id=api_id,
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH"),
            target_chat_id=target_id,
            telegram_session_path=session,
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=os.getenv("FFPROBE_PATH", "ffprobe"),
        )

    @classmethod
    def for_tests(cls, root: Path, *, device_token: str) -> "Settings":
        settings = cls(data_root=root, device_token=device_token)
        settings.create_directories()
        return settings

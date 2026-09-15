from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings
from .mdns import MdnsPublisher
from .repository import Repository


def load_settings(env_path: str) -> Settings:
    return Settings.from_env(Path(env_path).expanduser().resolve())


def initialize(settings: Settings) -> Repository:
    settings.validate()
    repository = Repository(settings)
    repository.initialize()
    repository.recover_inflight()
    return repository


def main() -> int:
    parser = argparse.ArgumentParser(prog="cardputer-agent")
    parser.add_argument("--env", default=".env")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve")
    subcommands.add_parser("status")
    subcommands.add_parser("list-pending")
    subcommands.add_parser("retry-failed")
    subcommands.add_parser("check-config")
    args = parser.parse_args()

    try:
        settings = load_settings(args.env)
        if args.command == "check-config":
            settings.validate(require_telegram=True)
            missing_tools = [tool for tool in (settings.ffmpeg_path, settings.ffprobe_path) if shutil.which(tool) is None]
            if missing_tools:
                raise RuntimeError(f"missing tools: {', '.join(missing_tools)}")
            settings.create_directories()
            print("configuration_ok=1")
            print(f"data_root={settings.data_root}")
            return 0

        repository = initialize(settings)
        if args.command == "status":
            counts = Counter(record.state for record in repository.list_by_states(
                ("receiving", "ready", "converting", "converted", "sending", "sent", "failed")
            ))
            print(json.dumps(dict(sorted(counts.items())), indent=2))
            return 0
        if args.command == "list-pending":
            for record in repository.list_by_states():
                print(f"{record.recording_id}\t{record.state}\tattempts={record.attempts}")
            return 0
        if args.command == "retry-failed":
            print(f"retried={repository.retry_failed()}")
            return 0
        if args.command == "serve":
            settings.validate(require_telegram=True)
            from .logging_setup import configure_service_logging

            configure_service_logging(settings)
            publisher = MdnsPublisher(settings.listen_port)
            publisher.start()
            try:
                uvicorn.run(
                    create_app(settings, run_worker=True),
                    host=settings.listen_host,
                    port=settings.listen_port,
                    access_log=False,
                    log_level="info",
                    log_config=None,
                )
            finally:
                publisher.close()
            return 0
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

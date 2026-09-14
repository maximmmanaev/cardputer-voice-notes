#!/usr/bin/env python3
"""Convert Spike A WAV to OGG/Opus and send it as a Telethon user voice note."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


class ProbeError(RuntimeError):
    pass


class ConfigurationError(RuntimeError):
    pass


def build_ffmpeg_command(source: Path, destination: Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "libopus",
        "-application",
        "voip",
        "-b:a",
        "24000",
        "-vbr",
        "on",
        "-compression_level",
        "10",
        str(destination),
    ]


def normalize_group_id(value: str) -> int:
    try:
        group_id = int(value.strip())
    except ValueError as error:
        raise ValueError("TARGET_CHAT_ID must be an integer") from error
    if group_id >= 0:
        raise ValueError("TARGET_CHAT_ID must be a negative Telegram group ID")
    return group_id


def run_ffprobe(path: Path) -> dict[str, Any]:
    process = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise ProbeError(f"ffprobe rejected OGG: {process.stderr.strip()}")
    return json.loads(process.stdout)


def validate_ogg(path: Path, *, source_duration: float, tolerance: float = 0.25) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        raise ProbeError("OGG is missing or empty")
    probe = run_ffprobe(path)
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ProbeError(f"expected one audio stream, found {len(streams)}")
    stream = streams[0]
    codec = stream.get("codec_name")
    channels = int(stream.get("channels", 0))
    sample_rate = int(stream.get("sample_rate", 0))
    format_name = probe.get("format", {}).get("format_name", "")
    duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)
    if codec != "opus":
        raise ProbeError(f"expected Opus codec, found {codec}")
    if channels != 1:
        raise ProbeError(f"expected mono, found {channels} channels")
    if sample_rate != 48_000:
        raise ProbeError(f"expected 48000 Hz Opus stream, found {sample_rate}")
    if "ogg" not in format_name:
        raise ProbeError(f"expected OGG container, found {format_name}")
    if abs(duration - source_duration) > tolerance:
        raise ProbeError(f"duration drift: source={source_duration:.3f}s opus={duration:.3f}s")
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "codec": codec,
        "sample_rate": sample_rate,
        "channels": channels,
        "duration": duration,
    }


def wav_duration(path: Path) -> float:
    probe = run_ffprobe(path)
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ProbeError(f"expected one source audio stream, found {len(streams)}")
    return float(streams[0].get("duration") or probe.get("format", {}).get("duration") or 0)


def convert_voice(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    duration = wav_duration(source)
    process = subprocess.run(build_ffmpeg_command(source, destination), check=False, capture_output=True, text=True)
    if process.returncode != 0:
        raise ProbeError(f"ffmpeg conversion failed: {process.stderr.strip()}")
    return validate_ogg(destination, source_duration=duration)


def required_environment() -> tuple[int, str, int, Path]:
    from dotenv import load_dotenv

    load_dotenv()
    missing = [name for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TARGET_CHAT_ID") if not os.getenv(name)]
    if missing:
        raise ConfigurationError(f"missing local configuration: {', '.join(missing)}")
    try:
        api_id = int(os.environ["TELEGRAM_API_ID"])
    except ValueError as error:
        raise ConfigurationError("TELEGRAM_API_ID must be numeric") from error
    api_hash = os.environ["TELEGRAM_API_HASH"]
    target_id = normalize_group_id(os.environ["TARGET_CHAT_ID"])
    session_path = Path(os.getenv("TELEGRAM_SESSION_PATH", "data/telegram/cardputer-recorder.session"))
    return api_id, api_hash, target_id, session_path


async def inspect_or_send(voice: Path, confirmed_chat_id: int | None, wait_seconds: int) -> int:
    from telethon import TelegramClient, types, utils

    api_id, api_hash, target_id, session_path = required_environment()
    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ConfigurationError("Telethon session is not authorized; run scripts/telegram_login.py")
        entity = await client.get_entity(target_id)
        is_group = isinstance(entity, types.Chat) or (
            isinstance(entity, types.Channel) and bool(getattr(entity, "megagroup", False))
        )
        if not is_group:
            raise ConfigurationError("TARGET_CHAT_ID does not resolve to a Telegram group")
        resolved_id = utils.get_peer_id(entity)
        title = getattr(entity, "title", "")
        print(f"TARGET_GROUP_TITLE={title}")
        print(f"TARGET_GROUP_ID={resolved_id}")
        if confirmed_chat_id is None:
            print("TARGET_CONFIRMATION_REQUIRED=1")
            return 0
        if confirmed_chat_id != resolved_id:
            raise ConfigurationError("confirmed chat ID does not match the resolved target group")

        message = await client.send_file(entity, str(voice), voice_note=True)
        print(f"TELEGRAM_MESSAGE_ID={message.id}")

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            async for candidate in client.iter_messages(entity, min_id=message.id, limit=20):
                sender = await candidate.get_sender()
                username = (getattr(sender, "username", "") or "").lower()
                if username == "smartspeech_sber_bot":
                    print(f"SALUTESPEECH_REPLY_MESSAGE_ID={candidate.id}")
                    return message.id
            await asyncio.sleep(3)
        raise RuntimeError("SaluteSpeech did not reply before timeout")
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--ogg", type=Path, default=Path("data/spikes/spike-b-voice.ogg"))
    parser.add_argument("--confirmed-chat-id", type=int)
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--convert-only", action="store_true")
    args = parser.parse_args()
    try:
        result = convert_voice(args.wav, args.ogg)
        print(json.dumps(result, indent=2))
        if not args.convert_only:
            asyncio.run(inspect_or_send(args.ogg, args.confirmed_chat_id, args.wait_seconds))
    except (ConfigurationError, ProbeError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

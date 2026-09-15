from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaMetadata:
    codec: str
    container: str
    channels: int
    sample_rate: int
    duration: float
    size: int


def _probe(path: Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    process = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise MediaError(f"ffprobe rejected media: {process.stderr.strip()}")
    return json.loads(process.stdout)


def _audio(probe: dict[str, Any]) -> tuple[dict[str, Any], str, float]:
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise MediaError(f"expected exactly one audio stream, found {len(streams)}")
    stream = streams[0]
    format_name = probe.get("format", {}).get("format_name", "")
    duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)
    return stream, format_name, duration


def validate_wav(path: Path, ffprobe: str = "ffprobe") -> MediaMetadata:
    stream, container, duration = _audio(_probe(path, ffprobe))
    if stream.get("codec_name") != "pcm_s16le":
        raise MediaError("WAV must contain signed 16-bit little-endian PCM")
    if int(stream.get("channels", 0)) != 1 or int(stream.get("sample_rate", 0)) != 16_000:
        raise MediaError("WAV must be mono at 16 kHz")
    if "wav" not in container:
        raise MediaError("input is not a WAV container")
    if duration <= 0:
        raise MediaError("WAV duration is invalid")
    return MediaMetadata("pcm_s16le", container, 1, 16_000, duration, path.stat().st_size)


def validate_ogg(path: Path, source_duration: float, ffprobe: str = "ffprobe") -> MediaMetadata:
    stream, container, duration = _audio(_probe(path, ffprobe))
    if stream.get("codec_name") != "opus":
        raise MediaError("output codec is not Opus")
    channels = int(stream.get("channels", 0))
    sample_rate = int(stream.get("sample_rate", 0))
    if channels != 1 or sample_rate != 48_000 or "ogg" not in container:
        raise MediaError("output must be mono 48 kHz OGG/Opus")
    if abs(duration - source_duration) > 0.25:
        raise MediaError(f"duration drift exceeds 250 ms: {source_duration:.3f}s -> {duration:.3f}s")
    if path.stat().st_size <= 0 or path.stat().st_size > 20 * 1024 * 1024:
        raise MediaError("output size is invalid for the configured voice pipeline")
    return MediaMetadata("opus", container, channels, sample_rate, duration, path.stat().st_size)


def convert_and_validate(
    source: Path,
    destination: Path,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> MediaMetadata:
    source_metadata = validate_wav(source, ffprobe)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    part = destination.with_suffix(destination.suffix + ".part")
    process = subprocess.run(
        [
            ffmpeg,
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
            "-f",
            "ogg",
            str(part),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise MediaError(f"ffmpeg conversion failed: {process.stderr.strip()}")
    metadata = validate_ogg(part, source_metadata.duration, ffprobe)
    part.replace(destination)
    return metadata

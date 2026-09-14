#!/usr/bin/env python3
"""Validate the physical Cardputer-Adv Spike A WAV with ffprobe."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


class ProbeError(RuntimeError):
    pass


def run_ffprobe(path: Path) -> dict[str, Any]:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise ProbeError(f"ffprobe rejected WAV: {process.stderr.strip()}")
    return json.loads(process.stdout)


def validate_wav(
    path: Path,
    *,
    expected_duration: float = 60.0,
    tolerance: float = 0.25,
) -> dict[str, Any]:
    if path.exists() and path.stat().st_size == 0:
        raise ProbeError("WAV is empty")

    probe = run_ffprobe(path)
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type", "audio") == "audio"]
    if len(streams) != 1:
        raise ProbeError(f"expected one audio stream, found {len(streams)}")

    stream = streams[0]
    codec = stream.get("codec_name")
    sample_rate = int(stream.get("sample_rate", 0))
    channels = int(stream.get("channels", 0))
    bits = int(stream.get("bits_per_sample", 0))
    format_name = probe.get("format", {}).get("format_name", "")
    duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)

    if codec != "pcm_s16le":
        raise ProbeError(f"expected pcm_s16le, found {codec}")
    if sample_rate != 16_000:
        raise ProbeError(f"expected 16000 Hz sample rate, found {sample_rate}")
    if channels != 1:
        raise ProbeError(f"expected mono, found {channels} channels")
    if bits != 16:
        raise ProbeError(f"expected 16-bit PCM, found {bits}")
    if "wav" not in format_name:
        raise ProbeError(f"expected WAV container, found {format_name}")
    if abs(duration - expected_duration) > tolerance:
        raise ProbeError(
            f"expected duration {expected_duration:.3f}s ± {tolerance:.3f}s, found {duration:.3f}s"
        )

    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size if path.exists() else None,
        "codec": codec,
        "sample_rate": sample_rate,
        "channels": channels,
        "bits_per_sample": bits,
        "duration": duration,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--expected-duration", type=float, default=60.0)
    parser.add_argument("--tolerance", type=float, default=0.25)
    args = parser.parse_args()
    try:
        print(json.dumps(validate_wav(
            args.wav,
            expected_duration=args.expected_duration,
            tolerance=args.tolerance,
        ), indent=2))
    except ProbeError as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


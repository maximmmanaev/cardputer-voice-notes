#!/usr/bin/env python3
"""Download the newest closed Spike A WAV from Cardputer-Adv over USB CDC."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import time
from pathlib import Path
from typing import BinaryIO


BEGIN_RE = re.compile(
    rb"^SPIKE_A_DOWNLOAD_BEGIN path=(/spike-a-[A-Za-z0-9-]+\.wav) size=([0-9]+)\r?\n$"
)


class DownloadError(RuntimeError):
    pass


def parse_begin_line(line: bytes) -> tuple[str, int]:
    match = BEGIN_RE.fullmatch(line)
    if match is None:
        raise DownloadError(f"unexpected download header: {line[:160]!r}")
    size = int(match.group(2))
    if size < 44:
        raise DownloadError(f"invalid WAV size: {size}")
    return match.group(1).decode("ascii"), size


def read_exact(stream: BinaryIO, size: int, *, chunk_size: int = 64 * 1024) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(min(chunk_size, remaining))
        if not chunk:
            raise DownloadError(f"serial payload ended with {remaining} bytes missing")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def wait_for_begin(port: BinaryIO, timeout: float) -> tuple[str, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline()
        if line.startswith(b"SPIKE_A_DOWNLOAD_BEGIN "):
            return parse_begin_line(line)
        if line.startswith(b"SPIKE_A_DOWNLOAD_ERROR="):
            raise DownloadError(line.decode("utf-8", errors="replace").strip())
    raise DownloadError("Cardputer did not start the USB download before timeout")


def wait_for_end(port: BinaryIO, expected_size: int, timeout: float) -> None:
    expected = f"SPIKE_A_DOWNLOAD_END size={expected_size}".encode()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port.readline().strip() == expected:
            return
    raise DownloadError("Cardputer did not confirm the completed USB download")


def download(port_name: str, output_dir: Path, timeout: float) -> Path:
    try:
        import serial
    except ImportError as error:
        raise DownloadError("pyserial is required; use the project's PlatformIO venv") from error

    port = serial.Serial()
    port.port = port_name
    port.baudrate = 115200
    port.timeout = 1.0
    port.write_timeout = 3.0
    port.open()
    try:
        port.reset_input_buffer()
        port.write(b"D\n")
        port.flush()
        remote_path, size = wait_for_begin(port, timeout)

        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = output_dir / Path(remote_path).name
        part = destination.with_suffix(destination.suffix + ".part")
        remaining = size
        digest = hashlib.sha256()
        with part.open("wb") as target:
            while remaining:
                chunk = port.read(min(64 * 1024, remaining))
                if not chunk:
                    raise DownloadError(f"serial payload ended with {remaining} bytes missing")
                target.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            target.flush()
            os.fsync(target.fileno())

        wait_for_end(port, size, timeout)
        os.replace(part, destination)
        print(f"downloaded={destination.resolve()}")
        print(f"size={size}")
        print(f"sha256={digest.hexdigest()}")
        return destination
    finally:
        port.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/spikes"))
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    try:
        download(args.port, args.output_dir, args.timeout)
    except DownloadError as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

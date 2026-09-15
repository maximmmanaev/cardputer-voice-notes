#!/usr/bin/env python3
"""Create ignored local Mac-agent and firmware configuration without echoing secrets."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import socket
import subprocess
from pathlib import Path

from dotenv import dotenv_values, set_key


ROOT = Path(__file__).resolve().parents[1]


def local_ipv4() -> str:
    # On macOS, the default route may point at a VPN/TUN adapter (for example
    # 198.18.0.0/15). Cardputer must receive the physical Wi-Fi address.
    for interface in ("en0", "en1", "en2", "en3"):
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface], check=False, capture_output=True, text=True
        )
        candidate = result.stdout.strip()
        if candidate.startswith(("10.", "192.168.")) or candidate.startswith(tuple(f"172.{item}." for item in range(16, 32))):
            return candidate
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 53))
        return probe.getsockname()[0]
    finally:
        probe.close()


def cpp_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    env_path = args.env.expanduser().resolve()
    existing = {key: value or "" for key, value in dotenv_values(env_path).items()}

    required_telegram = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TARGET_CHAT_ID", "TELEGRAM_SESSION_PATH")
    missing = [key for key in required_telegram if not existing.get(key)]
    if missing:
        raise SystemExit("Run scripts/telegram_login.py first; missing: " + ", ".join(missing))

    print("Enter the known Wi-Fi credentials locally. Values are hidden and never logged.")
    ssid = getpass.getpass("Wi-Fi SSID: ").strip()
    password = getpass.getpass("Wi-Fi password: ")
    if not ssid:
        raise SystemExit("Wi-Fi SSID cannot be empty")

    token = existing.get("DEVICE_TOKEN") or secrets.token_urlsafe(32)
    ip_address = local_ipv4()
    port = int(existing.get("LISTEN_PORT") or "8765")
    data_root = str((ROOT / "data" / "agent").resolve())
    updates = {
        "DEVICE_TOKEN": token,
        "LISTEN_HOST": ip_address,
        "LISTEN_PORT": str(port),
        "DATA_ROOT": data_root,
        "FFMPEG_PATH": "/opt/homebrew/bin/ffmpeg",
        "FFPROBE_PATH": "/opt/homebrew/bin/ffprobe",
    }
    env_path.touch(mode=0o600, exist_ok=True)
    for key, value in updates.items():
        set_key(str(env_path), key, value, quote_mode="always")
    os.chmod(env_path, 0o600)

    header = ROOT / "firmware" / "include" / "local_config.h"
    header.write_text(
        "#pragma once\n\n"
        f'#define RECORDER_WIFI_SSID "{cpp_string(ssid)}"\n'
        f'#define RECORDER_WIFI_PASSWORD "{cpp_string(password)}"\n'
        f'#define RECORDER_DEVICE_TOKEN "{cpp_string(token)}"\n'
        f'#define RECORDER_SYNC_FALLBACK_IP "{cpp_string(ip_address)}"\n'
        f"#define RECORDER_SYNC_PORT {port}\n",
        encoding="utf-8",
    )
    os.chmod(header, 0o600)
    print("Local configuration written with mode 0600.")
    print(f"Mac LAN endpoint: {ip_address}:{port}")
    print("No secret value was printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

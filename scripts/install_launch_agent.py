#!/usr/bin/env python3
"""Install and start the per-user macOS LaunchAgent."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.ganzoliki.cardputer-recorder"


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, text=True, capture_output=True)


def main() -> int:
    python = ROOT / ".venv" / "bin" / "python"
    env_path = ROOT / ".env"
    if not python.exists() or not env_path.exists():
        raise SystemExit("Create .venv and local .env before installing the LaunchAgent")
    validation = run(str(python), "-m", "cardputer_agent.cli", "--env", str(env_path), "check-config", check=False)
    if validation.returncode:
        raise SystemExit("Agent configuration is incomplete; run scripts/configure_local.py first")
    logs = ROOT / "data" / "agent" / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(logs, 0o700)
    destination = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "cardputer_agent.cli", "--env", str(env_path), "serve"],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "agent.stdout.log"),
        "StandardErrorPath": str(logs / "agent.stderr.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    temporary = destination.with_suffix(".plist.tmp")
    with temporary.open("wb") as output:
        plistlib.dump(payload, output, sort_keys=True)
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)

    domain = f"gui/{os.getuid()}"
    run("launchctl", "bootout", domain, str(destination), check=False)
    result = run("launchctl", "bootstrap", domain, str(destination), check=False)
    if result.returncode:
        raise SystemExit(f"launchctl bootstrap failed: {result.stderr.strip()}")
    run("launchctl", "kickstart", "-k", f"{domain}/{LABEL}")
    print(f"installed={destination}")
    print(f"label={LABEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

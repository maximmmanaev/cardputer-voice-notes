# Cardputer-Adv Recorder

Autonomous Cardputer-Adv recorder with LAN synchronization to a macOS agent and Telegram voice delivery through a Telethon user session.

The project is currently at the mandatory critical-path spike stage. Full firmware and macOS-agent implementation starts only after both hardware audio and Telegram-to-SaluteSpeech paths are proven. See [docs/spikes.md](docs/spikes.md).

## Spike A build

Requirements used on the development Mac:

- macOS 26.3.1 on Apple Silicon (`arm64`)
- Python 3.14.6 in an isolated project environment
- PlatformIO Core 6.2.0
- Espressif32 PlatformIO platform 6.7.0
- pinned M5Cardputer and M5Unified revisions from `firmware/platformio.ini`

Build:

```bash
python3 -m venv .venv-platformio
.venv-platformio/bin/python -m pip install platformio==6.2.0
.venv-platformio/bin/pio run -d firmware
```

No upload, flash erase, partition-table change or SD formatting is performed by the build. Hardware deployment requires identification and backup first.

## Privacy

Recording must be visible and consensual. Obtain consent from people being recorded and follow local law. Audio is local while it remains on Cardputer or Mac; after delivery to Telegram and SaluteSpeech it is processed by those services and is no longer fully local.


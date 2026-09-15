# Verified deployment — 2026-09-15

- Host: macOS 26.3.1(a), arm64; isolated Python 3.14.6 environment.
- Device: M5Stack Cardputer-Adv, ESP32-S3 revision 0.2, board ID 24, 8 MB flash.
- USB serial: `/dev/cu.usbmodem1101`; device MAC is reported externally only as `3c:0f:…:72:a8`.
- Full pre-write flash backup: local ignored `backups/` file with the device identifier masked, 8,388,608 bytes, SHA-256 `a4482fbecbc9fbd735ff6ec077303ac2e709064cca29df52625a5a1e0e6fc9db`. It does not include microSD.
- Installed firmware: `recorder-0.3.1`; boot heartbeat confirms board 24, `IDLE`, and mounted microSD.
- Flash operation wrote only the factory application range beginning at `0x10000`. Bootloader, partition table, NVS and microSD were not overwritten.
- Mac LaunchAgent: `com.ganzoliki.cardputer-recorder`, running and successfully restarted through `launchctl kickstart -k`.
- LAN receiver: physical Wi-Fi interface on port 8765; HTTP health and Bonjour `_cardputer-sync._tcp.local` passed. The precise current IP is local configuration because it may change through DHCP.
- Physical end-to-end recording: `rec-0f3c-b000001-s000001-f001`, PCM signed 16-bit little-endian, mono, 16 kHz, 12.736 s, 407,596 bytes.
- Converted voice: OGG/Opus, mono, 48 kHz, 12.7425 s, 33,113 bytes.
- Telegram sent-state: message `240648`, confirmed as an outgoing voice from the authorized user session. SaluteSpeech response: message `240649`. Message text was not copied into logs.
- Device logged a durable ACK only after the Mac flushed, hashed, atomically renamed, and committed the recording to SQLite.
- The initially observed `SYNC ERROR` was traced to relative filenames returned by the SD iterator. Firmware now normalizes every enumerated SD path before hashing/opening; the queued recording then synchronized without data loss.

Automated result: 26 tests passed. Coverage includes 30/70/99-percent resumable transfers, stale-offset recovery after deliberate socket cancellation, immutable upload metadata, invalid-token rejection, idempotent repeat, queue ordering, crash reconciliation without duplicate Telegram send, real ffmpeg/ffprobe validation, WAV recovery, and two-hour/24-fragment rotation simulation.

The physical two-hour endurance test and deliberate power-cut recovery test remain manual long-running checks. Their checklist is in the root README.

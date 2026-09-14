# Critical-path spikes

## Spike A — Cardputer-Adv audio to microSD

Status: **spike firmware is running on confirmed Cardputer-Adv with a writable 32 GB microSD; waiting for the physical 60-second recording/listening test**.

Official-source audit on 2026-09-14:

- Cardputer-Adv is SKU K132-Adv with Stamp-S3A / ESP32-S3FN8, 8 MB flash, ES8311, MEMS microphone and a microSD slot.
- Official audio pin map: I2C SDA 8, SCL 9; I2S BCLK 41, ADC data 46, LRCK 43, DAC data 42.
- Official microSD pin map: CS 12, MOSI 14, CLK 40, MISO 39.
- M5Cardputer commit `f1392858b9994c3547120e602a57d3553d16ab01` identifies `board_M5CardputerADV` and uses the TCA8418 keyboard driver.
- M5Unified 0.2.22 commit `e79eb6e3137a41e50b0ec23c8c38738bdb3a11e2` has a Cardputer-Adv-specific ES8311 capture callback and I2S mapping. Both revisions are pinned in `firmware/platformio.ini`.
- The official `mic_wav_record` example was inspected but not copied. It records `512 × 240` samples into an approximately 240 KiB RAM allocation before writing the file. The spike instead alternates two 1024-sample buffers and writes completed blocks directly to SD.

Build result:

- PlatformIO Core 6.2.0, Espressif32 platform 6.7.0 and Arduino-ESP32 2.0.16.
- Release build succeeded: 27,560 bytes RAM (8.4%) and 548,109 bytes flash (16.4%).
- The dependency graph used M5Cardputer `1.1.1+sha.f139285` and M5Unified `0.2.22+sha.e79eb6e`.
- The only warning was a Python `SyntaxWarning` inside the bundled esptool.py 4.5.1; there were no firmware compiler warnings or errors.
- Host-side WAV validator followed test-first: its initial run failed because the implementation did not exist; after implementation, 2/2 unit tests pass.
- The full local build log is stored outside git under `logs/spike-a-build-20260914.log`.

Connected-device probe:

- Serial port: `/dev/cu.usbmodem1101`; USB VID:PID `303A:1001`, description `USB JTAG/serial debug unit`.
- ROM: ESP32-S3 revision 0.2, 40 MHz crystal, Wi-Fi/BLE; MAC recorded locally and shown in reports only as `3c:0f:…:72:a8`.
- Flash: 8 MB, quad data lines.
- A complete read-only backup of `0x000000–0x7fffff` succeeded before any upload: 8,388,608 bytes, SHA-256 `a4482fbecbc9fbd735ff6ec077303ac2e709064cca29df52625a5a1e0e6fc9db`. The ignored `backups/` directory also contains the exact restore command. This backup does not include microSD.
- Before the upload, no erase or write operation had run. The factory image strings then confirmed Cardputer-Adv independently; the deployment below wrote only the application range after the full flash backup completed.

Deployment update:

- Factory-flash strings independently identified `cardputer-adv`, `CardputerADV`, TCA8418 and factory version `V0.9-36-ge824a76` before any write.
- The factory partition table is a single 4 MB `factory` app at `0x10000`; PlatformIO's generated OTA partition table differs. To preserve the factory layout, only `firmware.bin` was written at `0x10000`. Bootloader, partition table, NVS and microSD were not overwritten.
- esptool verified the written image hash. The running firmware reports `spike-a-0.1.0`, M5 board ID 24 (`board_M5CardputerADV`) and 31,902,269,440 free bytes on the inserted card.
- A periodic serial heartbeat was added after the first boot showed that one-shot messages can be lost during native USB re-enumeration.
- The remaining Spike A step is physical: press Enter once, speak for 60 seconds, then validate and listen to the closed WAV from the card.

Physical pass criteria:

1. USB VID/PID and ESP32-S3 bootloader identify the connected Cardputer-Adv; M5Unified reports `board_M5CardputerADV`.
2. Existing flash is backed up before the first upload.
3. With a user-provided FAT32 microSD inserted, Enter records exactly 960,000 mono samples and atomically renames `.wav.part` to `.wav`.
4. `scripts/validate_spike_wav.py` and ffprobe confirm PCM signed 16-bit little-endian, mono, 16 kHz and 60 seconds.
5. A human listening check confirms intelligible speech at normal speed and correct channel layout.

At 32,000 bytes/second, PCM uses 115.2 MB per hour and 9.6 MB per five-minute fragment. A nominal 32 GB card holds about 277.8 hours before filesystem overhead and the configured free-space reserve. Actual capacity will be reported from the inserted card; the firmware never formats it.

## Spike B — Telethon voice to SaluteSpeech

Status: **not started; gated on successful physical Spike A**.

The primary path will use Telegram's client API through a local Telethon user session. A Bot API sender will not be used. The physical test requires local interactive entry of Telegram credentials and confirmation of the exact target chat before the first send.

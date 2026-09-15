# macOS agent

FastAPI/uvicorn LAN receiver, SQLite durable queue, ffmpeg converter, and Telethon user-session sender. Setup, commands, state model, LaunchAgent operation, and recovery procedures are documented in the repository [README](../README.md).

The receiver authenticates every recording operation with a device bearer token. Uploads are resumable and idempotent by immutable `recording_id`, size, and SHA-256. A durable ACK is returned only after the complete file is flushed, hashed, atomically moved to `ready/`, and committed to SQLite.

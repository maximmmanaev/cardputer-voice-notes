from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

from .config import Settings
from .repository import Repository


RECORDING_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,94}[A-Za-z0-9])?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_app(settings: Settings, *, run_worker: bool = True) -> FastAPI:
    repository = Repository(settings)
    locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.validate()
        repository.initialize()
        repository.recover_inflight()
        worker_task = None
        if run_worker:
            from .worker import run_worker_loop

            worker_task = asyncio.create_task(run_worker_loop(settings, repository))
        yield
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Cardputer Recorder Agent", version="1", lifespan=lifespan)
    app.state.settings = settings
    app.state.repository = repository

    def authenticate(authorization: str | None) -> None:
        expected = f"Bearer {settings.device_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid device token")

    @app.get("/v1/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "version": 1}

    @app.get("/v1/recordings/{recording_id}")
    async def recording_status(recording_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
        authenticate(authorization)
        if not RECORDING_ID.fullmatch(recording_id) or ".." in recording_id:
            raise HTTPException(status_code=422, detail="invalid recording_id")
        record = repository.get(recording_id)
        if record is None:
            return {"recording_id": recording_id, "offset": 0, "durable_ack": False, "state": "missing"}
        if record.state == "receiving":
            part_path = settings.incoming_dir / f"{recording_id}.wav.part"
            actual_size = part_path.stat().st_size if part_path.exists() else 0
            if actual_size != record.received_size:
                repository.update_received(recording_id, actual_size)
                record = repository.get(recording_id)
                assert record is not None
        durable = record.state in {"ready", "converting", "converted", "sending", "sent"}
        return {
            "recording_id": recording_id,
            "offset": record.expected_size if durable else record.received_size,
            "durable_ack": durable,
            "state": record.state,
        }

    @app.put("/v1/recordings/{recording_id}")
    async def upload_recording(
        recording_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_upload_offset: str | None = Header(default=None),
        x_recording_size: str | None = Header(default=None),
        x_recording_sha256: str | None = Header(default=None),
        x_recording_filename: str | None = Header(default=None),
        x_duration_ms: str | None = Header(default=None),
    ) -> dict[str, object]:
        authenticate(authorization)
        if not RECORDING_ID.fullmatch(recording_id) or ".." in recording_id:
            raise HTTPException(status_code=422, detail="invalid recording_id")
        try:
            offset = int(x_upload_offset or "")
            expected_size = int(x_recording_size or "")
            duration_ms = int(x_duration_ms) if x_duration_ms else None
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid numeric upload header") from error
        if offset < 0 or expected_size < 44 or expected_size > settings.max_recording_bytes:
            raise HTTPException(status_code=422, detail="invalid upload size or offset")
        sha256 = (x_recording_sha256 or "").lower()
        if not SHA256.fullmatch(sha256):
            raise HTTPException(status_code=422, detail="invalid SHA-256")
        filename = x_recording_filename or ""
        if Path(filename).name != filename or not filename.lower().endswith(".wav") or len(filename) > 128:
            raise HTTPException(status_code=422, detail="invalid recording filename")

        lock = locks.setdefault(recording_id, asyncio.Lock())
        async with lock:
            try:
                record = repository.register_or_validate(
                    recording_id,
                    filename=filename,
                    expected_size=expected_size,
                    sha256=sha256,
                    duration_ms=duration_ms,
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if record.state != "receiving":
                return {
                    "recording_id": recording_id,
                    "offset": record.expected_size,
                    "durable_ack": True,
                    "already_accepted": True,
                    "state": record.state,
                }

            part_path = settings.incoming_dir / f"{recording_id}.wav.part"
            actual_offset = part_path.stat().st_size if part_path.exists() else 0
            if actual_offset != record.received_size:
                repository.update_received(recording_id, actual_offset)
            if offset != actual_offset:
                raise HTTPException(status_code=409, detail={"expected_offset": actual_offset})

            written = actual_offset
            try:
                with part_path.open("ab") as output:
                    async for chunk in request.stream():
                        if not chunk:
                            continue
                        if written + len(chunk) > expected_size:
                            raise HTTPException(status_code=413, detail="upload exceeds declared size")
                        output.write(chunk)
                        written += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                # Persist progress even when the Cardputer deliberately closes the
                # socket to cancel a sync or Wi-Fi disappears mid-request.
                durable_bytes = part_path.stat().st_size if part_path.exists() else written
                repository.update_received(recording_id, durable_bytes)

            if written < expected_size:
                return {
                    "recording_id": recording_id,
                    "offset": written,
                    "durable_ack": False,
                    "already_accepted": False,
                    "state": "receiving",
                }

            digest = hashlib.sha256()
            with part_path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), sha256):
                repository.set_state(recording_id, "failed", error="SHA-256 mismatch")
                raise HTTPException(status_code=422, detail="SHA-256 mismatch")

            ready_path = settings.ready_dir / f"{recording_id}.wav"
            os.replace(part_path, ready_path)
            _fsync_directory(settings.ready_dir)
            repository.mark_ready(recording_id, ready_path)
            return {
                "recording_id": recording_id,
                "offset": written,
                "durable_ack": True,
                "already_accepted": False,
                "state": "ready",
            }

    return app

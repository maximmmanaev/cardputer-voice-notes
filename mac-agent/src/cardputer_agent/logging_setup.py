from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .config import Settings


def configure_service_logging(settings: Settings) -> None:
    settings.create_directories()
    path = settings.logs_dir / "agent.log"
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    os.chmod(path, 0o600)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

"""Small, privacy-conscious logging setup for the desktop app."""

from __future__ import annotations

import logging
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(data_directory: Path) -> logging.Logger:
    """Configure a rotating local log and return the application logger."""

    logger = logging.getLogger("waitlab")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        log_directories = [
            data_directory / "logs",
            Path(tempfile.gettempdir()) / "WaitLAB" / "logs",
        ]
        for log_directory in log_directories:
            try:
                log_directory.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    log_directory / "waitlab.log",
                    maxBytes=1_000_000,
                    backupCount=3,
                    encoding="utf-8",
                )
            except OSError:
                continue
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            ))
            logger.addHandler(handler)
            break
        else:
            # Logging must never prevent the desktop app from starting.
            logger.addHandler(logging.NullHandler())
    return logger


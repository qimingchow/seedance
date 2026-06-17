"""Operational logging helpers."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str = "seedance") -> logging.Logger:
    """Return a configured app logger with console and optional file output."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    log_file = os.getenv("LOG_FILE", "logs/seedance.log").strip()
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=int(os.getenv("LOG_MAX_BYTES", "10485760")),
            backupCount=int(os.getenv("LOG_BACKUP_COUNT", "5")),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

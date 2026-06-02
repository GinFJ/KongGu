"""Logging setup for the Konggu desktop application."""

from __future__ import annotations

import logging
from pathlib import Path


LOGGER_NAME = "konggu"


def setup_logging(log_path: str | Path = "konggu.log") -> logging.Logger:
    """Configure and return the project logger.

    Repeated calls are safe; existing handlers are reused so GUI refreshes do
    not duplicate log lines.
    """

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    path = Path(log_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger

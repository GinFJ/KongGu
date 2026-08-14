"""Small helpers for keeping local processing responses privacy-safe."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n\"']+")
_POSIX_PATH = re.compile(r"(?<![\w])/(?:[^\s/]+/)+[^\s\"']*")


def safe_error_message(value: Any, fallback: str = "本地处理失败，请检查输入文件后重试。") -> str:
    """Return a useful error without exposing absolute local paths."""

    message = str(value or "").strip()
    if not message:
        return fallback
    message = _WINDOWS_PATH.sub("[本地文件]", message)
    message = _POSIX_PATH.sub("[本地文件]", message)
    return message[:500]


def safe_file_name(value: Any) -> str:
    """Use only a basename when a filename crosses a response boundary."""

    return Path(str(value or "")).name

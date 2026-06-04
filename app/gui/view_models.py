"""Small UI view models and event contracts for the desktop shell."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class UiState(StrEnum):
    IDLE = "IDLE"
    FILES_SELECTED = "FILES_SELECTED"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPORTED = "EXPORTED"


@dataclass(slots=True)
class UiEvent:
    event_type: str
    message: str = ""
    progress: float | None = None
    payload: Any = None
    level: str = "INFO"


@dataclass(slots=True)
class SummaryMetrics:
    pdf_count: int = 0
    recognized_members: int = 0
    complete_members: int = 0
    warning_count: int = 0

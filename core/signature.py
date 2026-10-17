"""Versioned parser signature used by caches, corrections and benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


PARSER_VERSION = "39"
PROFILE_VERSION = "1"
CONFIG_VERSION = "1"
DEFAULT_OCR_ENGINE = "paddle_v4"
DEFAULT_OCR_MODEL = "PP-OCRv4-mobile"
DEFAULT_SEMESTER_START_DATE = "2026-03-02"
DEFAULT_TEACHING_WEEKS = 18


@dataclass(frozen=True, slots=True)
class ParserSignature:
    parser_version: str
    profile_version: str
    ocr_engine: str
    ocr_model: str
    config_version: str
    config_digest: str
    # Effective semester calendar values.  They influence sheet dates, week
    # numbers and the year chosen for year-less dates, so they belong in the
    # signature even though they live in persisted user settings, not config/.
    calendar_start: str
    teaching_weeks: str

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return {**asdict(self), "digest": self.digest}


def _valid_calendar_start(value: object) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except Exception:
        return ""


def _valid_teaching_weeks(value: object) -> str:
    try:
        weeks = int(str(value))
    except Exception:
        return ""
    return str(weeks) if weeks > 0 else ""


def _effective_calendar(project_root: Path) -> tuple[str, str]:
    """Mirror ``schedule_core._semester_start_date`` and ``_teaching_weeks``.

    The signature must describe the *effective* calendar: persisted settings
    win over ``config/school_calendar.json``, which wins over the defaults.
    """

    start = _valid_calendar_start(os.environ.get("KONGGU_SEMESTER_START_DATE", ""))
    weeks = _valid_teaching_weeks(os.environ.get("KONGGU_TEACHING_WEEKS", ""))
    if start and weeks:
        return start, weeks
    payload: dict[str, object] = {}
    try:
        payload = json.loads((project_root / "config" / "school_calendar.json").read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not start:
        start = (
            _valid_calendar_start(payload.get("semester_start_date"))
            or DEFAULT_SEMESTER_START_DATE
        )
    if not weeks:
        weeks = _valid_teaching_weeks(payload.get("teaching_weeks")) or str(DEFAULT_TEACHING_WEEKS)
    return start, weeks


def build_parser_signature(
    *,
    root: Path | None = None,
    ocr_engine: str = DEFAULT_OCR_ENGINE,
    ocr_model: str = DEFAULT_OCR_MODEL,
    config_paths: Iterable[str] = (
        "config/timetable_layout_profiles.json",
        "config/period_time.json",
        "config/school_calendar.json",
        "config/ocr_models.json",
        "config/ocr_engines.json",
    ),
) -> ParserSignature:
    project_root = root or Path(__file__).resolve().parents[1]
    hasher = hashlib.sha256()
    for relative in sorted(config_paths):
        path = project_root / relative
        hasher.update(relative.encode("utf-8"))
        if path.exists():
            hasher.update(path.read_bytes())
        else:
            hasher.update(b"<missing>")
    calendar_start, teaching_weeks = _effective_calendar(project_root)
    return ParserSignature(
        parser_version=PARSER_VERSION,
        profile_version=PROFILE_VERSION,
        ocr_engine=ocr_engine,
        ocr_model=ocr_model,
        config_version=CONFIG_VERSION,
        config_digest=hasher.hexdigest(),
        calendar_start=calendar_start,
        teaching_weeks=teaching_weeks,
    )


def cache_signature() -> str:
    return build_parser_signature().digest

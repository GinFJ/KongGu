"""Versioned parser signature used by caches, corrections and benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable


PARSER_VERSION = "34"
PROFILE_VERSION = "1"
CONFIG_VERSION = "1"
DEFAULT_OCR_ENGINE = "paddle_v4"
DEFAULT_OCR_MODEL = "PP-OCRv4-mobile"


@dataclass(frozen=True, slots=True)
class ParserSignature:
    parser_version: str
    profile_version: str
    ocr_engine: str
    ocr_model: str
    config_version: str
    config_digest: str

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return {**asdict(self), "digest": self.digest}


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
    return ParserSignature(
        parser_version=PARSER_VERSION,
        profile_version=PROFILE_VERSION,
        ocr_engine=ocr_engine,
        ocr_model=ocr_model,
        config_version=CONFIG_VERSION,
        config_digest=hasher.hexdigest(),
    )


def cache_signature() -> str:
    return build_parser_signature().digest

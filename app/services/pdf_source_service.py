"""Service helpers for managing selected PDF schedule sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.legacy_adapter import pdf_source_from_path
from core.models import PdfSource, ScheduleSourceType


VALID_KINDS = {"中方", "英方"}


@dataclass(slots=True)
class PdfSourceAddResult:
    """Result of adding one or more PDF source paths."""

    added: list[PdfSource] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[Path, Exception]] = field(default_factory=list)


@dataclass(slots=True)
class ReferenceLibrarySummary:
    """Resolved reference library directory and its PDF count."""

    path: Path
    pdf_count: int


def add_pdf_sources(
    *,
    paths: list[str] | tuple[str, ...],
    explicit_kind: ScheduleSourceType | None,
    existing_sources: list[PdfSource],
    schedule_core: Any,
) -> PdfSourceAddResult:
    """Convert selected PDF paths to PdfSource objects, inferring kind when needed."""

    result = PdfSourceAddResult()
    existing = {(item.source_path, item.kind) for item in existing_sources}
    existing_hashes = {
        (item.content_hash, item.kind): item
        for item in existing_sources
        if item.content_hash
    }
    for path in paths:
        pdf_path = Path(path)
        if not pdf_path.exists():
            result.skipped.append(str(pdf_path))
            continue

        kind = explicit_kind or _infer_pdf_kind(schedule_core, pdf_path)
        if kind not in VALID_KINDS:
            result.errors.append(
                (
                    pdf_path,
                    ValueError("无法识别课表类型，请在文件名中标明“中方”或“英方”。"),
                )
            )
            continue
        if _looks_like_placeholder_file_name(pdf_path.name):
            result.errors.append(
                (
                    pdf_path,
                    ValueError("文件名包含测试或占位姓名“路人甲”，请使用真实成员课表文件。"),
                )
            )
            continue
        key = (str(pdf_path.resolve()), kind)
        if key in existing:
            result.skipped.append(str(pdf_path))
            continue

        try:
            source = pdf_source_from_path(pdf_path, kind)
        except Exception as exc:
            result.errors.append((pdf_path, exc))
            continue

        hash_key = (source.content_hash, source.kind)
        if source.content_hash and hash_key in existing_hashes:
            existing_source = existing_hashes[hash_key]
            existing_name = _extract_member_name(schedule_core, existing_source.file_name)
            new_name = _extract_member_name(schedule_core, source.file_name)
            if existing_name and new_name and existing_name != new_name:
                result.errors.append(
                    (
                        pdf_path,
                        ValueError(
                            f"同一份{source.kind}课表内容重复，但文件名成员不一致："
                            f"{existing_name} / {new_name}。请确认是否传错课表。"
                        ),
                    )
                )
            else:
                result.skipped.append(str(pdf_path))
            continue

        result.added.append(source)
        existing.add(key)
        if source.content_hash:
            existing_hashes[hash_key] = source
    return result


def _infer_pdf_kind(schedule_core: Any, pdf_path: Path) -> str:
    inferred = schedule_core.infer_pdf_kind(pdf_path.name, str(pdf_path))
    if inferred in VALID_KINDS:
        return inferred
    return inferred


def _looks_like_placeholder_file_name(file_name: str) -> bool:
    return "路人甲" in file_name


def _extract_member_name(schedule_core: Any, file_name: str) -> str:
    extractor = getattr(schedule_core, "_extract_name_from_filename", None)
    if callable(extractor):
        return str(extractor(file_name) or "").strip()
    return Path(file_name).stem


def load_reference_library_summary(reference_library_path: str) -> ReferenceLibrarySummary:
    """Resolve and summarize a configured reference library directory."""

    path = Path(reference_library_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return ReferenceLibrarySummary(path=path, pdf_count=len(list(path.rglob("*.pdf"))))

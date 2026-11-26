"""Service helpers for managing selected PDF schedule sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.identity import identity_from_filename
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
    selected_names: dict[str, Path] = {}
    for path in paths:
        pdf_path = Path(path)
        if not pdf_path.is_file():
            result.errors.append((pdf_path, ValueError("文件不存在或无法读取，请重新选择该课表。")))
            continue

        name_key = pdf_path.name.casefold()
        previous = selected_names.get(name_key)
        if previous is not None and previous.resolve() != pdf_path.resolve():
            result.errors.append((pdf_path, ValueError("存在同名课表文件，无法可靠对应原文；请先为文件使用不同名称。")))
            continue
        selected_names[name_key] = pdf_path

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
            conflict = duplicate_identity_error(existing_source.file_name, source.file_name, source.kind)
            if conflict:
                result.errors.append((pdf_path, ValueError(conflict)))
            else:
                result.skipped.append(str(pdf_path))
            continue

        result.added.append(source)
        existing.add(key)
        if source.content_hash:
            existing_hashes[hash_key] = source
    return result


def _infer_pdf_kind(schedule_core: Any, pdf_path: Path) -> str:
    return str(schedule_core.infer_pdf_kind(pdf_path.name, str(pdf_path)) or "")


def _looks_like_placeholder_file_name(file_name: str) -> bool:
    return "路人甲" in file_name


def duplicate_identity_error(first_name: str, second_name: str, kind: str) -> str:
    first = identity_from_filename(first_name)
    second = identity_from_filename(second_name)
    if first.name and first.member_key == second.member_key:
        return ""
    return (f"两份{kind}课表内容完全相同，但文件名成员不一致或身份不完整，无法区分原件归属。"
            f"「{first_name}」对应{first.display_name or '未知成员'}，"
            f"「{second_name}」对应{second.display_name or '未知成员'}。请核实两份原件后重新导入。")


def load_reference_library_summary(reference_library_path: str) -> ReferenceLibrarySummary:
    """Resolve and summarize a configured reference library directory."""

    path = Path(reference_library_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return ReferenceLibrarySummary(path=path, pdf_count=len(list(path.rglob("*.pdf"))))

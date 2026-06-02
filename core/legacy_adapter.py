"""Adapters between the recovered legacy core and the new dataclass models."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from .errors import ErrorType, ProcessError
from .models import (
    AvailabilitySlot,
    CourseBlock,
    FileProcessRecord,
    MemberSchedule,
    PdfSource,
    ProcessResult,
    ProcessSummary,
    ScheduleSourceType,
)


_ROLE_WORDS = {"部长", "干事", "副部长", "负责人", "成员", "中方课表", "英方课表", "课表", "办公室"}


def hash_bytes(data: bytes) -> str:
    """Return a stable SHA256 hash for cache keys and duplicate detection."""

    return hashlib.sha256(data).hexdigest()


def pdf_source_from_path(path: str | Path, kind: ScheduleSourceType) -> PdfSource:
    """Build a PdfSource from a selected local PDF path."""

    pdf_path = Path(path)
    data = pdf_path.read_bytes()
    return PdfSource(
        file_name=pdf_path.name,
        kind=kind,
        source_path=str(pdf_path.resolve()),
        content_hash=hash_bytes(data),
        bytes_data=data,
    )


def legacy_source_to_model(source: dict[str, Any]) -> PdfSource:
    """Convert the recovered core's source dict into PdfSource."""

    file_name = str(source.get("file_name") or Path(str(source.get("source_path", ""))).name)
    kind = source.get("kind") if source.get("kind") in {"中方", "英方"} else "中方"
    data = source.get("bytes") or source.get("bytes_data")
    if data is not None and not isinstance(data, bytes):
        data = bytes(data)
    return PdfSource(
        file_name=file_name,
        kind=kind,
        source_path=str(source.get("source_path") or file_name),
        content_hash=str(source.get("content_hash") or hash_bytes(data or b"")),
        bytes_data=data,
    )


def model_source_to_legacy(source: PdfSource) -> dict[str, Any]:
    """Convert PdfSource back to the legacy dict expected by app.pyc."""

    return {
        "file_name": source.file_name,
        "kind": source.kind,
        "bytes": source.bytes_data,
        "source_path": source.source_path,
    }


def model_sources_to_legacy(sources: Iterable[PdfSource]) -> list[dict[str, Any]]:
    """Convert multiple PdfSource objects for the recovered parsing core."""

    return [model_source_to_legacy(source) for source in sources]


def infer_member_name_from_filename(file_name: str) -> str | None:
    """Best-effort member name inference from common department-role filenames."""

    stem = Path(file_name).stem
    normalized = stem.replace("-", " ").replace("_", " ")
    parts = [part.strip() for part in normalized.split() if part.strip()]
    for part in parts:
        if part in _ROLE_WORDS:
            continue
        cleaned = part.replace("中方课表", "").replace("英方课表", "").replace("课表", "").strip()
        if 2 <= len(cleaned) <= 4 and cleaned not in _ROLE_WORDS:
            return cleaned
    return None


def course_block_from_legacy(block: dict[str, Any]) -> CourseBlock:
    """Normalize one legacy course block into CourseBlock."""

    periods = block.get("periods", [])
    if not isinstance(periods, list):
        periods = [periods]
    source_type = block.get("source_type") or block.get("kind") or "中方"
    if source_type not in {"中方", "英方"}:
        source_type = "中方"
    return CourseBlock(
        name=str(block.get("name", "")).strip(),
        source_type=source_type,
        week=int(block.get("week")),
        weekday=str(block.get("weekday", "")).strip(),
        periods=[int(period) for period in periods if str(period).strip()],
        date=str(block.get("date")).strip() if block.get("date") else None,
        start_time=str(block.get("start_time")).strip() if block.get("start_time") else None,
        end_time=str(block.get("end_time")).strip() if block.get("end_time") else None,
        source_file=str(block.get("source_file")).strip() if block.get("source_file") else None,
    )


def course_blocks_from_legacy(blocks: Iterable[dict[str, Any]]) -> list[CourseBlock]:
    """Convert legacy blocks while skipping malformed rows."""

    converted: list[CourseBlock] = []
    for block in blocks:
        try:
            converted.append(course_block_from_legacy(block))
        except Exception:
            continue
    return converted


def build_member_schedules(blocks: Iterable[CourseBlock], known_names: Iterable[str]) -> list[MemberSchedule]:
    """Build member completeness rows from normalized course blocks."""

    by_name: dict[str, MemberSchedule] = {
        name: MemberSchedule(name=name) for name in sorted({name for name in known_names if name})
    }
    for block in blocks:
        if not block.name:
            continue
        member = by_name.setdefault(block.name, MemberSchedule(name=block.name))
        member.blocks.append(block)
        if block.source_type == "中方":
            member.has_chinese = True
            member.chinese_status = "已导入"
        elif block.source_type == "英方":
            member.has_english = True
            member.english_status = "已导入"

    for member in by_name.values():
        if member.has_chinese and member.has_english:
            member.status = "完整"
        elif member.has_chinese:
            member.status = "缺英方"
        elif member.has_english:
            member.status = "缺中方"
        else:
            member.status = "待处理"
    return list(by_name.values())


def build_file_records(
    sources: Iterable[PdfSource],
    blocks: Iterable[CourseBlock],
    errors: Iterable[str],
) -> list[FileProcessRecord]:
    """Build one processing record for each selected PDF."""

    error_texts = [str(error) for error in errors]
    block_list = list(blocks)
    records: list[FileProcessRecord] = []
    for source in sources:
        inferred_member = infer_member_name_from_filename(source.file_name)
        related_blocks = [
            block
            for block in block_list
            if block.source_file
            and (block.source_file == source.file_name or block.source_file == source.source_path)
        ]
        if not related_blocks and inferred_member:
            related_blocks = [
                block
                for block in block_list
                if block.source_type == source.kind and block.name == inferred_member
            ]
        related_errors = [
            text for text in error_texts if source.file_name in text or source.source_path in text
        ]
        first_member = next((block.name for block in related_blocks if block.name), inferred_member)
        if related_errors:
            error = ProcessError(
                error_type=ErrorType.CHINESE_PARSE_FAILED if source.kind == "中方" else ErrorType.ENGLISH_DATE_MAPPING_FAILED,
                message=related_errors[0],
                source_file=source.file_name,
            )
            records.append(
                FileProcessRecord(
                    source=source,
                    status="解析失败",
                    member_name=first_member,
                    detected_kind=source.kind,
                    block_count=len(related_blocks),
                    error=error,
                )
            )
        else:
            records.append(
                FileProcessRecord(
                    source=source,
                    status="已识别" if related_blocks else "待处理",
                    member_name=first_member,
                    detected_kind=source.kind,
                    text_source="未知",
                    block_count=len(related_blocks),
                )
            )
    return records


def availability_slot_from_row(row: dict[str, Any]) -> AvailabilitySlot:
    """Convert one empty-schedule table row into AvailabilitySlot."""

    free_members = [item for item in str(row.get("空闲人员", "")).split("、") if item]
    busy_members = [item for item in str(row.get("有课人员", "")).split("、") if item]
    return AvailabilitySlot(
        week=int(row.get("周次", 0)),
        date=str(row.get("日期", "")),
        weekday=str(row.get("星期", "")),
        period=int(row.get("节次", 0)),
        time_range=str(row.get("时间", "")),
        free_members=free_members,
        busy_members=busy_members,
    )


def build_process_result(
    *,
    blocks: list[CourseBlock],
    members: list[MemberSchedule],
    file_records: list[FileProcessRecord],
    slots: list[AvailabilitySlot] | None = None,
    logs: list[str] | None = None,
    calendar_rows: list[dict[str, Any]] | None = None,
) -> ProcessResult:
    """Create a full ProcessResult summary object for UI and export layers."""

    failed_files = [record for record in file_records if record.status == "解析失败"]
    summary = ProcessSummary(
        member_count=len(members),
        complete_member_count=sum(1 for member in members if member.status == "完整"),
        pending_member_count=sum(1 for member in members if member.status != "完整"),
        failed_file_count=len(failed_files),
        source_file_count=len(file_records),
        slot_count=len(slots or []),
    )
    return ProcessResult(
        blocks=blocks,
        members=members,
        file_records=file_records,
        slots=slots or [],
        failed_files=failed_files,
        logs=logs or [],
        calendar_rows=calendar_rows or [],
        summary=summary,
    )

"""Adapters between the recovered legacy core and the new dataclass models."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from .errors import ErrorType, ProcessError
from .identity import identity_from_filename
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


_ROLE_WORDS = {
    "办公室",
    "外联部",
    "宣传部",
    "活动部",
    "部长",
    "副部长",
    "干事",
    "负责人",
    "成员",
    "中方课表",
    "英方课表",
    "课表",
}
_SCHEDULE_WORDS = {"中方", "英方", "中方课表", "英方课表", "课表"}


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
        "content_hash": source.content_hash,
    }


def model_sources_to_legacy(sources: Iterable[PdfSource]) -> list[dict[str, Any]]:
    """Convert multiple PdfSource objects for the recovered parsing core."""

    return [model_source_to_legacy(source) for source in sources]


def infer_member_name_from_filename(file_name: str) -> str | None:
    """Best-effort member name inference from common department-role filenames."""

    stem = Path(file_name).stem
    normalized = re.sub(r"[-_\s]+", " ", stem)
    parts = [part.strip() for part in normalized.split() if part.strip()]
    for part in parts:
        if part in _ROLE_WORDS:
            continue
        cleaned = _clean_member_candidate(part)
        if _is_member_name_candidate(cleaned):
            return cleaned

    cleaned_stem = stem
    for word in sorted(_ROLE_WORDS | _SCHEDULE_WORDS, key=len, reverse=True):
        cleaned_stem = cleaned_stem.replace(word, " ")
    for token in re.findall(r"[\u4e00-\u9fff]{2,4}", cleaned_stem):
        if _is_member_name_candidate(token):
            return token
    return None


def _clean_member_candidate(text: str) -> str:
    cleaned = text
    for word in sorted(_SCHEDULE_WORDS, key=len, reverse=True):
        cleaned = cleaned.replace(word, "")
    return re.sub(r"[^\u4e00-\u9fff]", "", cleaned).strip()


def _is_member_name_candidate(text: str) -> bool:
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text) and text not in _ROLE_WORDS and text not in _SCHEDULE_WORDS)


def course_block_from_legacy(block: dict[str, Any]) -> CourseBlock:
    """Normalize one legacy course block into CourseBlock."""

    periods = block.get("periods", [])
    if not isinstance(periods, list):
        periods = [periods]
    source_type = block.get("source_type") or block.get("kind") or "中方"
    if source_type not in {"中方", "英方"}:
        source_type = "中方"
    name = str(block.get("name", "")).strip()
    source_file = str(block.get("source_file")).strip() if block.get("source_file") else None
    identity = identity_from_filename(source_file or "", fallback_name=name)
    bbox_value = block.get("bbox")
    if bbox_value is None and all(block.get(key) is not None for key in ("x0", "y0", "x1", "y1")):
        bbox_value = (block["x0"], block["y0"], block["x1"], block["y1"])
    bbox = None
    if isinstance(bbox_value, (list, tuple)) and len(bbox_value) >= 4:
        bbox = tuple(float(value) for value in bbox_value[:4])
    return CourseBlock(
        name=name,
        source_type=source_type,
        week=int(block.get("week")),
        weekday=str(block.get("weekday", "")).strip(),
        periods=[int(period) for period in periods if str(period).strip()],
        date=str(block.get("date")).strip() if block.get("date") else None,
        start_time=str(block.get("start_time")).strip() if block.get("start_time") else None,
        end_time=str(block.get("end_time")).strip() if block.get("end_time") else None,
        course=str(block.get("course")).strip() if block.get("course") else None,
        source_file=source_file,
        text_source=_normalize_text_source(block.get("text_source")),
        block_id=str(block.get("block_id") or ""),
        member_key=str(
            block.get("member_key")
            or (name if identity.confirmation_required else identity.member_key)
        ),
        department=str(block.get("department") or identity.department or "").strip() or None,
        role=str(block.get("role") or identity.role or "").strip() or None,
        source_hash=str(block.get("source_hash") or "").strip() or None,
        page=int(block["page"]) if block.get("page") is not None else None,
        bbox=bbox,
        confidence=float(block["confidence"]) if block.get("confidence") is not None else None,
        provenance=dict(block.get("provenance") or {}),
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

    block_list = list(blocks)
    by_key: dict[str, MemberSchedule] = {}
    for block in block_list:
        if not block.name:
            continue
        key = block.member_key or block.name
        member = by_key.setdefault(
            key,
            MemberSchedule(
                name=block.name,
                department=block.department,
                role=block.role,
                member_key=key,
            ),
        )
        member.blocks.append(block)
        if block.source_type == "中方":
            member.has_chinese = True
            member.chinese_status = "已导入"
        elif block.source_type == "英方":
            member.has_english = True
            member.english_status = "已导入"

    known = sorted({name for name in known_names if name})
    represented_names = {member.name for member in by_key.values()}
    represented_keys = {member.member_key for member in by_key.values()}
    for name in known:
        if name not in represented_names and name not in represented_keys:
            by_key[name] = MemberSchedule(name=name)

    for member in by_key.values():
        if member.has_chinese and member.has_english:
            member.status = "完整"
        elif member.has_chinese:
            member.status = "缺英方"
        elif member.has_english:
            member.status = "缺中方"
        else:
            member.status = "待处理"
        member.errors.extend(_detect_member_conflicts(member))
    return list(by_key.values())


def _detect_member_conflicts(member: MemberSchedule) -> list[ProcessError]:
    slots: dict[tuple[int, str, int], list[CourseBlock]] = {}
    for block in member.blocks:
        for period in block.periods:
            slots.setdefault((block.week, block.weekday, int(period)), []).append(block)

    errors: list[ProcessError] = []
    for (week, weekday, period), blocks in sorted(slots.items()):
        course_names = sorted({_course_label(block) for block in blocks if _course_label(block)})
        source_keys = {
            (block.source_type, block.source_file or "", _course_label(block))
            for block in blocks
        }
        if len(source_keys) <= 1 or len(course_names) <= 1:
            continue
        errors.append(
            ProcessError(
                error_type=ErrorType.SCHEDULE_CONFLICT,
                message=f"{member.name} 第{week}周{weekday}第{period}节存在多门课程：{'、'.join(course_names)}",
            )
        )
    return errors


def _course_label(block: CourseBlock) -> str:
    return str(getattr(block, "course", "") or block.source_file or block.source_type).strip()


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
            error_type = _error_type_for_message(source.kind, related_errors[0])
            error = ProcessError(
                error_type=error_type,
                message=related_errors[0],
                source_file=source.file_name,
            )
            records.append(
                FileProcessRecord(
                    source=source,
                    status="解析失败",
                    member_name=first_member,
                    detected_kind=source.kind,
                    text_source="OCR" if error_type in {ErrorType.OCR_CONFIG_FAILED, ErrorType.OCR_FAILED} else "未知",
                    used_ocr=error_type in {ErrorType.OCR_CONFIG_FAILED, ErrorType.OCR_FAILED},
                    block_count=len(related_blocks),
                    error=error,
                    source_hash=source.content_hash,
                    quality_state="blocked",
                )
            )
        else:
            text_source = _record_text_source(related_blocks)
            records.append(
                FileProcessRecord(
                    source=source,
                    status="已识别" if related_blocks else "待处理",
                    member_name=first_member,
                    detected_kind=source.kind,
                    text_source=text_source,
                    used_ocr=text_source == "OCR",
                    block_count=len(related_blocks),
                    source_hash=source.content_hash,
                    quality_state="accepted" if related_blocks else "blocked",
                )
            )
    return records


def _normalize_text_source(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"内嵌文本", "OCR", "缓存"}:
        return text
    return "未知"


def _record_text_source(blocks: list[CourseBlock]) -> str:
    sources = {getattr(block, "text_source", "未知") for block in blocks}
    if "OCR" in sources:
        return "OCR"
    if "内嵌文本" in sources:
        return "内嵌文本"
    if "缓存" in sources:
        return "缓存"
    return "未知"


def _error_type_for_message(kind: ScheduleSourceType, message: str) -> ErrorType:
    if "OCR 配置异常" in message:
        return ErrorType.OCR_CONFIG_FAILED
    if "OCR" in message or "图片型 PDF" in message or "扫描件" in message:
        return ErrorType.OCR_FAILED
    return ErrorType.CHINESE_PARSE_FAILED if kind == "中方" else ErrorType.ENGLISH_DATE_MAPPING_FAILED


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
    complete_members = sum(1 for member in members if member.is_complete)
    summary = ProcessSummary(
        member_count=len(members),
        complete_member_count=complete_members,
        pending_member_count=max(0, len(members) - complete_members),
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

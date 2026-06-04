"""JSON serializers and Web diagnostics for Konggu results."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import math
from pathlib import Path
from typing import Any

import pandas as pd

from app.web.task_manager import TaskLogRecord, UploadedFileRecord
from core.models import AvailabilitySlot, FileProcessRecord, MemberSchedule, PdfSource, ProcessResult


CHINESE_KIND_VALUES = {"chinese", "中方", "涓柟"}
ENGLISH_KIND_VALUES = {"english", "英方", "鑻辨柟"}
FAILED_STATUS_VALUES = {"解析失败", "瑙ｆ瀽澶辫触", "FAILED", "failed"}


def json_safe(value: Any) -> Any:
    """Convert common Python and pandas values into JSON-safe data."""

    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if is_dataclass(value):
        return json_safe(asdict(value))
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def dataframe_records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return [json_safe(row) for row in df.to_dict("records")]


def normalize_source_type(value: str | None, *, filename: str = "", path: str = "") -> str:
    """Return the Web API source-type enum: chinese / english / unknown."""

    raw = f"{value or ''} {filename} {path}".lower()
    if any(token in raw for token in ("english", "英方", "鑻辨柟", "uk")):
        return "english"
    if any(token in raw for token in ("chinese", "中方", "涓柟")):
        return "chinese"
    return "unknown"


def source_type_label(source_type: str) -> str:
    return {"chinese": "中方", "english": "英方", "unknown": "未知"}.get(source_type, "未知")


def serialize_pdf_source(source: PdfSource) -> dict[str, Any]:
    source_type = normalize_source_type(source.kind, filename=source.file_name, path=source.source_path)
    return {
        "filename": source.file_name,
        "path": source.source_path,
        "source_type": source_type,
        "source_type_label": source_type_label(source_type),
        "content_hash": source.content_hash,
        "status": "uploaded",
    }


def serialize_uploaded_file(file: UploadedFileRecord) -> dict[str, Any]:
    return {
        "filename": file.filename,
        "path": file.path,
        "size": file.size,
        "source_type": file.source_type or "unknown",
        "source_type_label": source_type_label(file.source_type),
        "member_name": file.member_name or "",
        "inferred_member": file.member_name or "",
        "status": file.status,
        "note": file.note,
        "warning": file.warning,
    }


def serialize_log(log: TaskLogRecord | str) -> dict[str, str]:
    if isinstance(log, TaskLogRecord):
        return {"time": log.time, "level": log.level, "message": log.message}
    return {"time": "", "level": "INFO", "message": str(log)}


def _member_warning_summary(member: MemberSchedule) -> str:
    errors = getattr(member, "errors", []) or []
    if not errors:
        return ""
    return "；".join(str(error.to_user_message() if hasattr(error, "to_user_message") else error) for error in errors)


def _member_risk(member: MemberSchedule) -> tuple[str, str]:
    block_count = len(member.blocks)
    warning = _member_warning_summary(member)
    has_chinese = bool(member.has_chinese)
    has_english = bool(member.has_english)

    if warning:
        return "需检查", warning
    if has_chinese and has_english and block_count > 0:
        return "正常", "-"
    if not has_chinese and not has_english and member.name:
        return "异常", "未找到有效课表"
    if block_count == 0:
        return "疑似解析失败", "没有识别到课程时间块"
    if not has_chinese:
        return "需检查", "缺少中方课表"
    if not has_english:
        return "需检查", "缺少英方课表"
    return "未知", member.display_remark or "-"


def serialize_member(member: MemberSchedule) -> dict[str, Any]:
    status, risk = _member_risk(member)
    return {
        "member": member.name,
        "chinese_schedule": "已导入" if member.has_chinese else "缺失",
        "english_schedule": "已导入" if member.has_english else "缺失",
        "course_block_count": len(member.blocks),
        "status": status,
        "risk": risk,
    }


def serialize_file_record(record: FileProcessRecord) -> dict[str, Any]:
    error = record.error.to_user_message() if record.error and hasattr(record.error, "to_user_message") else ""
    warning = record.display_result or ""
    source_type = normalize_source_type(record.display_kind, filename=record.source.file_name, path=record.source.source_path)
    return {
        "filename": record.source.file_name,
        "member": record.member_name or "未识别",
        "source_type": source_type,
        "source_type_label": source_type_label(source_type),
        "parser": record.text_source or "暂未提供",
        "cache": "暂未提供",
        "course_block_count": record.block_count,
        "status": record.status or "",
        "warning": warning,
        "error": error,
    }


def serialize_slot(slot: AvailabilitySlot) -> dict[str, Any]:
    return {
        "teaching_week": slot.week,
        "date": slot.date,
        "weekday": slot.weekday,
        "period": slot.period,
        "time_range": slot.time_range,
        "free_count": slot.free_count,
        "free_members": "、".join(slot.free_members),
        "busy_count": slot.busy_count,
        "busy_members": "、".join(slot.busy_members),
    }


def file_record_has_warning(record: FileProcessRecord) -> bool:
    status = str(record.status or "")
    return bool(record.error) or status in FAILED_STATUS_VALUES or record.block_count == 0


def result_summary(result: ProcessResult | None, *, pdf_count: int = 0, warning_count: int = 0) -> dict[str, int]:
    if result is None:
        return {
            "pdf_count": pdf_count,
            "member_count": 0,
            "complete_member_count": 0,
            "warning_count": warning_count,
            "course_block_count": 0,
            "availability_slot_count": 0,
        }
    file_warning_count = sum(1 for record in result.file_records if file_record_has_warning(record))
    member_warning_count = sum(1 for member in result.members if _member_risk(member)[0] != "正常")
    return {
        "pdf_count": result.summary.source_file_count or pdf_count,
        "member_count": result.summary.member_count,
        "complete_member_count": sum(1 for member in result.members if _member_risk(member)[0] == "正常"),
        "warning_count": warning_count + file_warning_count + member_warning_count,
        "course_block_count": len(result.blocks),
        "availability_slot_count": result.summary.slot_count,
    }


def acceptance_summary(
    *,
    result: ProcessResult | None,
    uploaded_count: int,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, int]:
    warnings = warnings or []
    errors = errors or []
    if result is None:
        return {
            "uploaded_pdf_count": uploaded_count,
            "successful_file_count": 0,
            "failed_file_count": 0,
            "member_count": 0,
            "complete_member_count": 0,
            "missing_chinese_count": 0,
            "missing_english_count": 0,
            "warning_count": len(warnings) + len(errors),
        }

    failed_file_count = sum(1 for record in result.file_records if file_record_has_warning(record))
    successful_file_count = max(0, len(result.file_records) - failed_file_count)
    missing_chinese_count = sum(1 for member in result.members if not member.has_chinese)
    missing_english_count = sum(1 for member in result.members if not member.has_english)
    complete_member_count = sum(1 for member in result.members if _member_risk(member)[0] == "正常")
    summary = result_summary(result, pdf_count=uploaded_count, warning_count=len(warnings) + len(errors))
    return {
        "uploaded_pdf_count": uploaded_count,
        "successful_file_count": successful_file_count,
        "failed_file_count": failed_file_count,
        "member_count": len(result.members),
        "complete_member_count": complete_member_count,
        "missing_chinese_count": missing_chinese_count,
        "missing_english_count": missing_english_count,
        "warning_count": summary["warning_count"],
    }


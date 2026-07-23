"""JSON-safe serializers for the Konggu desktop sidecar."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import math
from pathlib import Path
from typing import Any

import pandas as pd

from core.models import AvailabilitySlot, FileProcessRecord, MemberSchedule, ProcessResult


FAILED_STATUS_VALUES = {"解析失败", "FAILED", "failed"}


def json_safe(value: Any) -> Any:
    """Convert common Python and pandas values into JSON-safe objects."""

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


def file_record_has_warning(record: FileProcessRecord) -> bool:
    status = str(record.status or "")
    return bool(record.error) or status in FAILED_STATUS_VALUES or record.block_count == 0


def serialize_member(member: MemberSchedule) -> dict[str, Any]:
    status, risk = _member_risk(member)
    return {
        "member": member.name,
        "member_key": member.member_key,
        "department": member.department or "",
        "role": member.role or "",
        "chinese_schedule": "已导入" if member.has_chinese else "缺失",
        "english_schedule": "已导入" if member.has_english else "缺失",
        "course_block_count": len(member.blocks),
        "status": status,
        "risk": risk,
    }


def serialize_file_record(record: FileProcessRecord) -> dict[str, Any]:
    error = record.error.to_user_message() if record.error and hasattr(record.error, "to_user_message") else ""
    return {
        "filename": record.source.file_name,
        "member": record.member_name or "未识别",
        "source_type": record.display_kind,
        "parser": record.text_source or "暂未提供",
        "cache": "暂未提供",
        "course_block_count": record.block_count,
        "status": record.status or "",
        "quality_state": record.quality_state,
        "layout_profile": record.layout_profile or "",
        "source_hash": record.source_hash or record.source.content_hash or "",
        "warning": record.display_result or "",
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


def serialize_process_result(
    *,
    result: ProcessResult,
    preview_df: pd.DataFrame,
    warnings: list[str],
    errors: list[str],
    result_ref: str,
) -> dict[str, Any]:
    summary = result_summary(result, pdf_count=result.summary.source_file_count, warning_count=len(warnings) + len(errors))
    return {
        "ok": True,
        "result_ref": result_ref,
        "quality_state": result.quality_state,
        "can_export": result.quality_state == "accepted",
        "parser_signature": result.parser_signature,
        "summary": summary,
        "availability": [serialize_slot(slot) for slot in result.slots],
        "availability_preview": dataframe_records(preview_df),
        "members": [serialize_member(member) for member in result.members],
        "details": [serialize_file_record(record) for record in result.file_records],
        "courses": [json_safe(block) for block in result.blocks],
        "issues": [json_safe(issue) for issue in result.issues],
        "corrections": [json_safe(correction) for correction in result.corrections],
        "logs": list(result.logs),
        "warnings": warnings,
        "errors": errors,
    }


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

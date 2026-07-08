"""UI-neutral Konggu desktop workflow services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4
import pickle

import pandas as pd

from app.services.availability_preview_service import build_availability_preview
from app.services.desktop_serialization import file_record_has_warning, serialize_process_result
from app.services.export_excel_service import ExcelExportRequest, build_export_excel_bytes
from app.services.generate_availability_service import AvailabilityGenerationResult, generate_availability
from app.services.pdf_source_service import add_pdf_sources
from app.services.result_view_service import build_gui_process_result
from core.models import ProcessResult


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass(slots=True)
class DesktopWorkflowResult:
    """Full result retained for desktop preview and export."""

    generation_result: AvailabilityGenerationResult
    process_result: ProcessResult
    preview_df: pd.DataFrame
    warnings: list[str]
    errors: list[str]


def parse_pdf_paths(*, schedule_core: Any, paths: list[str], explicit_kind: str | None = None) -> DesktopWorkflowResult:
    """Parse selected PDFs and produce preview/export-ready data."""

    pdf_paths = [str(Path(path)) for path in paths if str(path).strip()]
    if not pdf_paths:
        raise ValueError("请先选择课表 PDF。")

    non_pdf = [path for path in pdf_paths if Path(path).suffix.lower() != ".pdf"]
    if non_pdf:
        raise ValueError("仅支持 PDF 文件：" + "、".join(Path(path).name for path in non_pdf))

    add_result = add_pdf_sources(
        paths=pdf_paths,
        explicit_kind=explicit_kind if explicit_kind in {"中方", "英方"} else None,
        existing_sources=[],
        schedule_core=schedule_core,
    )
    errors = [f"{path.name}: {exc}" for path, exc in add_result.errors]
    warnings = [f"已跳过：{item}" for item in add_result.skipped]
    if not add_result.added:
        if errors:
            raise ValueError("没有可解析的课表 PDF：" + "；".join(errors))
        raise ValueError("PDF 文件读取失败，请检查文件是否可访问。")

    generation = generate_availability(
        schedule_core=schedule_core,
        selected_sources=add_result.added,
        root_dir="",
        weekdays=WEEKDAYS,
    )
    preview = build_availability_preview(
        occupancy=generation.occupancy,
        students=generation.students,
        weeks=generation.weeks,
        weekdays=WEEKDAYS,
        calendar_df=generation.calendar_df,
        timetable_df=_load_default_timetable(schedule_core),
    )
    process_result = build_gui_process_result(
        course_blocks=generation.course_blocks,
        members=generation.member_schedules,
        file_records=generation.file_records,
        slots=preview.slots,
        student_count=len(generation.students),
        block_count=len(generation.blocks),
        week_count=len(generation.weeks),
        calendar_df=generation.calendar_df,
    )
    warnings = _collect_result_warnings(process_result, [*warnings, *generation.errors])
    errors.extend(generation.errors or [])

    return DesktopWorkflowResult(
        generation_result=generation,
        process_result=process_result,
        preview_df=preview.free_df,
        warnings=list(dict.fromkeys(warnings)),
        errors=list(dict.fromkeys(errors)),
    )


def export_excel(
    *,
    schedule_core: Any,
    workflow_result: DesktopWorkflowResult,
    target_path: str,
    mode: str = "classic",
    export_week_count: int | None = None,
) -> Path:
    """Export the parsed availability result to an Excel file."""

    if mode != "classic":
        raise ValueError("visual 导出模式已预留，等待可视化表格样式后实现。")
    target = Path(target_path)
    if target.suffix.lower() != ".xlsx":
        target = target.with_suffix(".xlsx")
    target.parent.mkdir(parents=True, exist_ok=True)
    generation = workflow_result.generation_result
    export_weeks = _export_weeks(generation.weeks, export_week_count)
    data = build_export_excel_bytes(
        schedule_core=schedule_core,
        request=ExcelExportRequest(
            occupancy=generation.occupancy,
            students=generation.students,
            weeks=export_weeks,
            calendar_df=generation.calendar_df,
            timetable_df=schedule_core.default_timetable(),
            threshold=0,
            blocks_df=generation.blocks_df,
            all_slot_df=generation.all_slot_df,
        ),
    )
    target.write_bytes(data)
    return target


def save_workflow_result(workflow_result: DesktopWorkflowResult, cache_root: Path) -> Path:
    """Persist a parse result so a later sidecar command can export it."""

    result_dir = cache_root / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / f"{uuid4().hex}.pkl"
    with path.open("wb") as file:
        pickle.dump(workflow_result, file)
    return path


def load_workflow_result(path: str | Path) -> DesktopWorkflowResult:
    """Load a previously persisted desktop workflow result."""

    result_path = Path(path)
    with result_path.open("rb") as file:
        loaded = pickle.load(file)
    if not isinstance(loaded, DesktopWorkflowResult):
        raise ValueError("解析结果引用无效，请重新解析后导出。")
    return loaded


def serialize_workflow_result(workflow_result: DesktopWorkflowResult, result_ref: str) -> dict[str, Any]:
    payload = serialize_process_result(
        result=workflow_result.process_result,
        preview_df=workflow_result.preview_df,
        warnings=workflow_result.warnings,
        errors=workflow_result.errors,
        result_ref=result_ref,
    )
    detected_weeks = _detected_weeks(workflow_result.generation_result.blocks)
    payload["detected_weeks"] = detected_weeks
    payload["detected_week_count"] = len(detected_weeks)
    payload["detected_max_week"] = max(detected_weeks) if detected_weeks else 0
    return payload


def _load_default_timetable(schedule_core: Any) -> pd.DataFrame:
    try:
        timetable = schedule_core.default_timetable()
        timetable, _ = schedule_core.validate_timetable(timetable)
        return timetable
    except Exception:
        return pd.DataFrame([{"period": i, "start": "", "end": ""} for i in [1, 2, 3, 4, 12, 13, 5, 6, 7, 8, 9, 10, 11]])


def _collect_result_warnings(result: ProcessResult, generation_errors: list[str]) -> list[str]:
    warnings = list(generation_errors or [])
    for record in result.file_records:
        if file_record_has_warning(record):
            message = record.display_result or "未识别到有效课程块"
            warnings.append(f"{record.source.file_name}: {message}")
    return [str(warning) for warning in warnings if warning]


def _detected_weeks(blocks: list[dict[str, Any]]) -> list[int]:
    weeks: set[int] = set()
    for block in blocks:
        try:
            weeks.add(int(block.get("week")))
        except Exception:
            continue
    return sorted(weeks)


def _export_weeks(default_weeks: list[int], export_week_count: int | None) -> list[int]:
    if export_week_count is None:
        return list(default_weeks)
    try:
        count = int(export_week_count)
    except Exception:
        return list(default_weeks)
    if count <= 0:
        return list(default_weeks)
    return list(range(1, min(count, 30) + 1))

"""UI-neutral Konggu desktop workflow services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import json

import pandas as pd

from app.services.availability_preview_service import build_availability_preview
from app.services.desktop_serialization import file_record_has_warning, serialize_process_result
from app.services.export_excel_service import ExcelExportRequest, build_export_excel_bytes
from app.services.generate_availability_service import AvailabilityGenerationResult, generate_availability
from app.services.pdf_source_service import add_pdf_sources
from app.services.result_view_service import build_gui_process_result
from app.services.state_store import StateStore
from app.services.workflow_persistence import restore_workflow, snapshot_workflow
from core.quality import assert_export_allowed, evaluate_quality
from core.signature import build_parser_signature
from core.runtime_control import cancellation_scope
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


def parse_pdf_paths(
    *,
    schedule_core: Any,
    paths: list[str],
    explicit_kind: str | None = None,
    enforce_quality: bool = False,
    progress: Callable[[str, int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> DesktopWorkflowResult:
    """Parse selected PDFs and produce preview/export-ready data."""

    pdf_paths = [str(Path(path)) for path in paths if str(path).strip()]
    if not pdf_paths:
        raise ValueError("请先选择课表 PDF。")

    non_pdf = [path for path in pdf_paths if Path(path).suffix.lower() != ".pdf"]
    if non_pdf:
        raise ValueError("仅支持 PDF 文件：" + "、".join(Path(path).name for path in non_pdf))
    if cancelled and cancelled():
        raise InterruptedError("任务已取消。")
    if progress:
        progress("text_layer", 0, len(pdf_paths), "正在读取 PDF 文本层并识别课表类型")

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

    if progress:
        progress("course_parse", 0, len(add_result.added), "正在解析课程占用槽")
    with cancellation_scope(cancelled):
        generation = generate_availability(
            schedule_core=schedule_core,
            selected_sources=add_result.added,
            root_dir="",
            weekdays=WEEKDAYS,
        )
    if cancelled and cancelled():
        raise InterruptedError("任务已取消。")
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
    signature = build_parser_signature()
    quality_state, issues = evaluate_quality(
        file_records=process_result.file_records,
        members=process_result.members,
        enforce_identity=enforce_quality,
    )
    process_result.quality_state = quality_state
    process_result.issues = issues
    process_result.parser_signature = signature.digest
    if progress:
        progress(
            "review" if quality_state != "accepted" else "completed",
            len(add_result.added),
            len(add_result.added),
            "解析完成，等待人工复核" if quality_state != "accepted" else "解析与质量门禁已通过",
        )

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
    assert_export_allowed(
        workflow_result.process_result.quality_state,
        workflow_result.process_result.issues,
    )
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
            member_course_df=generation.blocks_df,
            issue_df=pd.DataFrame(
                [
                    {
                        "问题ID": issue.issue_id,
                        "级别": issue.severity,
                        "错误码": issue.code,
                        "来源文件": issue.source_file or "",
                        "字段": issue.field or "",
                        "问题": issue.message,
                        "修复建议": issue.suggestion,
                        "已确认": "是" if issue.confirmed else "否",
                    }
                    for issue in workflow_result.process_result.issues
                ]
            ),
            file_df=pd.DataFrame(
                [
                    {
                        "文件名": record.source.file_name,
                        "成员": record.member_name or "",
                        "类型": record.display_kind,
                        "状态": record.status,
                        "质量状态": record.quality_state,
                        "课程块数": record.block_count,
                        "来源哈希": record.source_hash or record.source.content_hash or "",
                        "说明": record.display_result,
                    }
                    for record in workflow_result.process_result.file_records
                ]
            ),
            correction_df=pd.DataFrame(
                [
                    {
                        "来源哈希": correction.source_hash,
                        "课程块ID": correction.block_id,
                        "字段": correction.field,
                        "原值": json.dumps(correction.original_value, ensure_ascii=False),
                        "新值": json.dumps(correction.new_value, ensure_ascii=False),
                        "原因": correction.reason,
                        "操作者": correction.operator_id,
                        "时间": correction.created_at,
                        "是否过期": "是" if correction.stale else "否",
                    }
                    for correction in workflow_result.process_result.corrections
                ]
            ),
            instructions_df=pd.DataFrame(
                [
                    {"项目": "质量状态", "说明": workflow_result.process_result.quality_state},
                    {"项目": "解析器签名", "说明": workflow_result.process_result.parser_signature},
                    {"项目": "数据范围", "说明": "仅包含本次导入并通过质量门禁的课表。"},
                    {"项目": "隐私", "说明": "原始 PDF 未复制到数据库或工作簿。"},
                    {"项目": "复核", "说明": "问题和人工修正分别记录在对应工作表。"},
                ]
            ),
        ),
    )
    target.write_bytes(data)
    return target


def save_workflow_result(
    workflow_result: DesktopWorkflowResult,
    store: StateStore,
    job_id: str | None = None,
    request: dict[str, Any] | None = None,
) -> str:
    """Persist a versioned JSON result under a stable job UUID."""

    result_id = job_id or store.create_job(
        request or {},
        workflow_result.process_result.parser_signature,
        [source.source_path for source in workflow_result.generation_result.model_sources],
    )
    workflow_result.process_result.job_id = result_id
    snapshot = snapshot_workflow(workflow_result)
    store.update_job(
        result_id,
        status="completed",
        result_json=json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        error="",
        quality_state=workflow_result.process_result.quality_state,
        current=len(workflow_result.generation_result.model_sources),
        total=len(workflow_result.generation_result.model_sources),
    )
    store.replace_issues(result_id, snapshot.get("issues", []))
    return result_id


def load_workflow_result(result_ref: str, store: StateStore, schedule_core: Any) -> DesktopWorkflowResult:
    """Load a JSON workflow snapshot by stable job UUID."""

    job = store.get_job(result_ref)
    if not job or not job.get("result"):
        raise ValueError("解析结果引用无效或来自旧版，请重新解析后导出。")
    return restore_workflow(job["result"], schedule_core)


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

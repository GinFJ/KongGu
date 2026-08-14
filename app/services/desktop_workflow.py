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
from app.privacy import safe_error_message
from core.quality import assert_export_allowed, evaluate_quality
from core.signature import build_parser_signature
from core.runtime_control import cancellation_scope
from core.models import ParseIssue, ProcessResult
from core.pdf_inspection import MAX_PDF_BYTES, inspect_pdf_sources


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MAX_INPUT_FILES = 32
MAX_TOTAL_PDF_BYTES = 200 * 1024 * 1024
MAX_TOTAL_PDF_PAGES = 1000


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
    if len(pdf_paths) > MAX_INPUT_FILES:
        raise ValueError(f"一次最多处理 {MAX_INPUT_FILES} 份 PDF。")

    non_pdf = [path for path in pdf_paths if Path(path).suffix.lower() != ".pdf"]
    if non_pdf:
        raise ValueError("仅支持 PDF 文件：" + "、".join(Path(path).name for path in non_pdf))
    if cancelled and cancelled():
        raise InterruptedError("处理已取消。")
    existing_paths = [Path(path) for path in pdf_paths if Path(path).is_file()]
    oversized = [path.name for path in existing_paths if path.stat().st_size > MAX_PDF_BYTES]
    if oversized:
        raise ValueError(f"PDF 超过单文件大小上限（{MAX_PDF_BYTES // 1024 // 1024} MB）：" + "、".join(oversized))
    total_bytes = sum(path.stat().st_size for path in existing_paths)
    if total_bytes > MAX_TOTAL_PDF_BYTES:
        raise ValueError(f"本批 PDF 总大小不能超过 {MAX_TOTAL_PDF_BYTES // 1024 // 1024} MB。")
    if progress:
        progress("text_layer", 0, len(pdf_paths), "正在读取课表文字并判断中方、英方课表")

    add_result = add_pdf_sources(
        paths=pdf_paths,
        explicit_kind=explicit_kind if explicit_kind in {"中方", "英方"} else None,
        existing_sources=[],
        schedule_core=schedule_core,
    )
    if cancelled and cancelled():
        raise InterruptedError("处理已取消。")
    errors = [f"{path.name}: {safe_error_message(exc)}" for path, exc in add_result.errors]
    warnings = [f"已跳过：{item}" for item in add_result.skipped]
    preflight_issues = [
        ParseIssue(
            code="INPUT_PREFLIGHT_FAILED",
            message=safe_error_message(exc),
            severity="error",
            source_file=path.name,
            suggestion="核对成员身份、课表类型和文件来源后重新导入整批课表。",
            blocks_export=True,
        )
        for path, exc in add_result.errors
    ]
    if not add_result.added:
        if errors:
            raise ValueError("没有可解析的课表 PDF：" + "；".join(errors))
        raise ValueError("PDF 文件读取失败，请检查文件是否可访问。")

    if progress:
        progress("pdf_inspection", 0, len(add_result.added), "正在检查文件是否需要图片文字识别")
    pdf_inspections = inspect_pdf_sources(add_result.added)
    if cancelled and cancelled():
        raise InterruptedError("处理已取消。")

    inspection_issues: list[ParseIssue] = []
    ready_sources = []
    for source, inspection in zip(add_result.added, pdf_inspections):
        if inspection.get("status") == "ready":
            ready_sources.append(source)
            continue
        source_file = source.file_name
        message = str(inspection.get("error") or "PDF 结构检查未通过，已阻止继续解析。")
        inspection_issues.append(
            ParseIssue(
                code="PDF_INSPECTION_BLOCKED",
                message=message,
                severity="error",
                source_file=source_file,
                suggestion="请重新导出可读取、未加密且未超过资源限制的 PDF。",
                blocks_export=True,
            )
        )
        errors.append(f"{source_file}：{message}")
    total_pages = sum(int(item.get("page_count") or 0) for item in pdf_inspections)
    if total_pages > MAX_TOTAL_PDF_PAGES:
        raise ValueError(f"本批 PDF 总页数不能超过 {MAX_TOTAL_PDF_PAGES} 页。")
    if not ready_sources:
        raise ValueError("没有通过 PDF 安全检查的课表，已阻止继续解析。")

    if progress:
        progress("course_parse", 0, len(ready_sources), "正在整理课程时间")
    with cancellation_scope(cancelled):
        generation = generate_availability(
            schedule_core=schedule_core,
            selected_sources=ready_sources,
            root_dir="",
            weekdays=WEEKDAYS,
            pdf_inspections=pdf_inspections,
            progress=progress,
        )
    if cancelled and cancelled():
        raise InterruptedError("处理已取消。")
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
    warnings.extend(_inspection_warnings(generation.pdf_inspections))
    errors.extend(generation.errors or [])
    signature = build_parser_signature()
    quality_state, issues = evaluate_quality(
        file_records=process_result.file_records,
        members=process_result.members,
        inherited_issues=[*preflight_issues, *inspection_issues],
        enforce_identity=enforce_quality,
    )
    process_result.quality_state = quality_state
    process_result.issues = issues
    process_result.parser_signature = signature.digest
    if progress:
        progress(
            "review" if quality_state != "accepted" else "completed",
            len(ready_sources),
            len(ready_sources),
            "课表已处理，请核对标记内容" if quality_state != "accepted" else "空课表已生成，可以导出",
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
    store.save_job_result(
        result_id,
        result_json=json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        error="",
        quality_state=workflow_result.process_result.quality_state,
        current=len(workflow_result.generation_result.model_sources),
        total=len(workflow_result.generation_result.model_sources),
        issues=snapshot.get("issues", []),
    )
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
    inspection_by_hash = {
        str(item.get("source_hash") or ""): item
        for item in workflow_result.generation_result.pdf_inspections
        if item.get("source_hash")
    }
    inspection_by_name = {
        str(item.get("source_file") or ""): item
        for item in workflow_result.generation_result.pdf_inspections
        if item.get("source_file")
    }
    for detail in payload.get("details", []):
        inspection = inspection_by_hash.get(str(detail.get("source_hash") or "")) or inspection_by_name.get(
            str(detail.get("filename") or "")
        )
        if not inspection:
            continue
        detail.update(
            {
                "pdf_type": inspection.get("pdf_type", "unknown"),
                "page_count": inspection.get("page_count", 0),
                "ocr_pages": ", ".join(str(page) for page in inspection.get("pages_needing_ocr", [])) or "无需",
                "encoding_issues": "是" if inspection.get("has_encoding_issues") else "否",
                "inspection_engine": inspection.get("engine", ""),
                "inspection_status": inspection.get("status", "unavailable"),
            }
        )
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
            message = record.display_result or "未识别到有效课程时间"
            warnings.append(f"{record.source.file_name}: {message}")
    return [str(warning) for warning in warnings if warning]


def _inspection_warnings(inspections: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for inspection in inspections:
        source_file = str(inspection.get("source_file") or "PDF")
        if inspection.get("has_encoding_issues"):
            warnings.append(f"{source_file}: 直接读取的文字可能异常，请在原文核对中检查标记页面。")
        if inspection.get("status") != "ready":
            message = str(inspection.get("error") or "未能完成结构检查")
            warnings.append(f"{source_file}: {message}")
        elif inspection.get("warning"):
            warnings.append(f"{source_file}: {inspection['warning']}")
    return warnings


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

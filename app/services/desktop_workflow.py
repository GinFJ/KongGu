"""UI-neutral Konggu desktop workflow services."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import json
import hashlib
import os

import pandas as pd

from app.services.availability_preview_service import build_availability_preview
from app.services.department_availability import build_department_scopes
from app.services.desktop_serialization import file_record_has_warning, serialize_process_result
from app.services.course_verification import workflow_verification
from app.services.date_review import member_counts, selected_calendar, workflow_view
from app.services.review_operations import correction_export_values
from app.services.export_excel_service import ExcelExportRequest, build_export_excel_bytes
from app.services.generate_availability_service import AvailabilityGenerationResult, generate_availability
from app.services.pdf_source_service import add_pdf_sources
from app.services.result_view_service import build_gui_process_result
from app.services.state_store import StateStore
from app.services.workflow_persistence import restore_workflow, snapshot_workflow
from app.privacy import safe_error_message
from core.errors import ErrorType, ProcessError
from core.legacy_adapter import infer_member_name_from_filename
from core.quality import SCHEDULE_RANGE_SUGGESTION, assert_export_allowed, evaluate_quality, schedule_range_violations
from core.signature import build_parser_signature
from core.runtime_control import cancellation_scope
from core.models import FileProcessRecord, ParseIssue, ProcessResult
from core.pdf_inspection import MAX_PDF_BYTES, inspect_pdf_sources


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# A complete student-organisation batch commonly contains more than 32 PDFs.
# Parsing stays sequential; the independent byte/page ceilings still apply.
MAX_INPUT_FILES = 128
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
    verified_items: set[str] = field(default_factory=set)
    date_review: dict[str, Any] | None = None


def parse_pdf_paths(
    *,
    schedule_core: Any,
    paths: list[str],
    explicit_kind: str | None = None,
    enforce_quality: bool = False,
    progress: Callable[[str, int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> DesktopWorkflowResult:
    """Parse selected PDFs and produce preview/export-ready data.

    ``progress(stage, current, total, message)`` reports a 0-based index into
    the *selected* ``paths`` list (never into a preflight-filtered subset), so
    callers can map an event back to the file row they created for that path.
    """

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
        progress("pdf_inspection", 0, len(pdf_paths), "正在检查文件是否需要图片文字识别")
    pdf_inspections = inspect_pdf_sources(add_result.added)
    if cancelled and cancelled():
        raise InterruptedError("处理已取消。")

    inspection_issues: list[ParseIssue] = []
    ready_sources = []
    for source, inspection in zip(add_result.added, pdf_inspections):
        if inspection.get("status") == "ready":
            ready_sources.append(source)
            if inspection.get("clipped_pages"):
                inspection_issues.append(
                    ParseIssue(
                        code="PDF_PAGE_CONTENT_CLIPPED",
                        message="课表内容超出 PDF 页面边界，页面外课程不可见，不能判为空闲。",
                        severity="error",
                        source_file=source.file_name,
                        source_hash=source.content_hash,
                        suggestion="重新导出能完整显示全部课表列和课程的 PDF。",
                        blocks_export=True,
                    )
                )
            if source.kind == "中方" and inspection.get("pages_needing_ocr"):
                inspection_issues.append(
                    ParseIssue(
                        code="CHINESE_IMAGE_PAGE_REVIEW",
                        message="中方课表含图片页，需核对表格四边、全部日期和课程是否完整显示。",
                        severity="warning",
                        source_file=source.file_name,
                        source_hash=source.content_hash,
                        suggestion="在原文核对中查看整页；若有裁切，请重新导出完整课表。",
                        blocks_export=True,
                    )
                )
            midday_check = getattr(schedule_core, "english_unmapped_midday_cell_count", None)
            if source.kind == "英方" and callable(midday_check):
                count = midday_check(
                    {
                        "path": source.source_path,
                        "bytes_data": source.bytes_data,
                        "name": source.file_name,
                        "content_hash": source.content_hash,
                    }
                )
                if count:
                    inspection_issues.append(
                        ParseIssue(
                            code="ENGLISH_UNNUMBERED_MIDDAY",
                            message=f"英方课表有 {count} 个午间课程格未印节次和时钟时间，现有时间轴无法可靠定位。",
                            severity="error",
                            source_file=source.file_name,
                            source_hash=source.content_hash,
                            suggestion="核对原页午间课程的准确时间后，才能生成可信空课。",
                            blocks_export=True,
                        )
                    )
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
        # 逐文件原因必须留给用户：只说“没有通过安全检查”无法定位是哪个文件。
        details = list(
            dict.fromkeys(
                [
                    *errors,
                    *[
                        f"{item.get('source_file')}：{item.get('error') or 'PDF 结构检查未通过'}"
                        for item in pdf_inspections
                        if item.get("status") != "ready"
                    ],
                ]
            )
        )
        reason = "；".join(details) if details else "未提供可解析的 PDF。"
        raise ValueError(f"没有通过 PDF 安全检查的课表：{reason}")
    ready_positions = _selected_positions(pdf_paths, ready_sources)

    def report_ready_progress(stage: str, current: int, total: int, message: str) -> None:
        """Translate the parser's ready-source index back to a selected path index."""

        if not progress:
            return
        progress(stage, _selected_index(current, ready_positions, message), len(pdf_paths), message)

    if progress:
        report_ready_progress("course_parse", 0, len(ready_sources), "正在整理课程时间")
    with cancellation_scope(cancelled):
        generation = generate_availability(
            schedule_core=schedule_core,
            selected_sources=ready_sources,
            root_dir="",
            weekdays=WEEKDAYS,
            pdf_inspections=pdf_inspections,
            progress=report_ready_progress,
        )
    notice_evidence = getattr(schedule_core, "visual_notice_consensus_evidence", None)
    if any(i.get('date_coverage') is not None for i in generation.pdf_inspections):
        allowed = '、'.join(map(str, generation.weeks))
        inspection_issues.append(ParseIssue(
            code='TIMETABLE_COVERAGE_LIMIT' if generation.weeks else 'TIMETABLE_COVERAGE_EMPTY',
            message=(f'原课表共同覆盖的完整工作周为第 {allowed} 周；空课预览和导出仅包含这些周次。'
                     if generation.weeks else '原课表没有共同覆盖的完整工作周，不能生成可靠空课表。'),
            severity='info' if generation.weeks else 'error',
            suggestion='其他周次缺少英方原课表数据，不能视为空闲；需要时补充对应日期的原课表。',
            blocks_export=not bool(generation.weeks),
        ))
    for inspection in generation.pdf_inspections:
        for note in inspection.get('english_grid_evidence', []):
            if note.get('source_hash') != inspection.get('source_hash'):
                raise ValueError('英方课程格证据与课表来源不一致，请重新解析。')
            is_overlay = note.get('kind') == 'overlay'
            page_label = f"第 {int(note.get('page', 0))+1} 页"
            if note.get('kind') == 'unresolved_date_row':
                inspection_issues.append(ParseIssue(
                    code='ENGLISH_DATE_ROW_UNRESOLVED',
                    message=f'{page_label}有可见日期行未能与星期可靠配对，该行不能当作空课。',
                    severity='error',source_file=inspection.get('source_file'),source_hash=note['source_hash'],
                    suggestion='请在原文核对中检查该行的日期和星期，必要时重新导出清晰完整的原课表。',
                    blocks_export=True,
                ))
                continue
            if note.get('kind') == 'overlay_verified_empty':
                inspection_issues.append(ParseIssue(
                    code='ENGLISH_OVERLAY_EMPTY_VERIFIED',
                    message=f'{page_label}的课程详情浮层已核验：原 PDF 保留的完整底层格子为空，未计为课程占用。',
                    severity='info',source_file=inspection.get('source_file'),source_hash=note['source_hash'],
                    suggestion='可在原文核对中查看浮层及对应日期；原件或解析规则变化后会重新核验。',
                    blocks_export=False,
                ))
                continue
            inspection_issues.append(ParseIssue(
                code='ENGLISH_GRID_OVERLAY' if is_overlay else 'ENGLISH_UNNUMBERED_MIDDAY',
                message=(f'{page_label}有悬浮课程详情覆盖表格，遮挡区域不能可靠判断为空闲。' if is_overlay
                         else f"{page_label}，{note.get('date', '')} 的午间课程格没有对应节次和时间，不能套用下午节次。"),
                severity='error', source_file=inspection.get('source_file'), source_hash=note['source_hash'],
                suggestion=('请移开鼠标、关闭悬浮详情后重新导出完整课表 PDF。' if is_overlay
                            else '请核实原课表午间列的起止时间，补全时间映射后重新计算。'),
                blocks_export=True,
            ))
    for source, inspection in zip(add_result.added, pdf_inspections):
        if source not in ready_sources:
            continue
        if "nonclass_consensus" in inspection:
            # Generation retains in-memory evidence when a parse-cache write
            # fails. A fresh source dict must not erase it with an empty read.
            notes = deepcopy(inspection["nonclass_consensus"])
        elif callable(notice_evidence):
            notes = deepcopy(notice_evidence({
                "file_name": source.file_name,
                "kind": source.kind,
                "source_path": source.source_path,
                "bytes_data": source.bytes_data,
                "content_hash": source.content_hash,
            }))
        else:
            continue
        if not notes:
            continue
        if any(note.get("source_hash") != source.content_hash for note in notes):
            raise ValueError("非课标记推断证据与课表来源不一致，请重新解析。")
        inspection["nonclass_consensus"] = notes
        inspection_issues.extend(
            _notice_consensus_issue(source.file_name, source.content_hash, note)
            for note in notes
        )
        notes = [note for note in notes if note.get('requires_review', True)]
        if not notes:
            continue
        previous = str(inspection.get("warning") or "").strip()
        labels = "、".join(sorted({str(note.get("canonical_label") or "非课标记") for note in notes}))
        excluded = sum(note.get('action') == 'excluded_from_preview_occupancy' for note in notes)
        notice_warning = (f"有 {len(notes)} 处“{labels}”标记需要核对，其中 {excluded} 处预览已按非课程排除。"
                          if excluded else f"有 {len(notes)} 处疑似“{labels}”的标记，预览仍保留原占用，请核对原页。")
        inspection["warning"] = "；".join(part for part in [previous, notice_warning] if part)
    english_ocr_files = {
        block.source_file for block in generation.course_blocks
        if block.source_type == "英方" and block.text_source == "OCR"
    }
    for source, inspection in zip(add_result.added, pdf_inspections):
        if source.kind == "英方" and source in ready_sources and (
            inspection.get("pages_needing_ocr") or source.file_name in english_ocr_files
        ):
            inspection_issues.append(
                ParseIssue(
                    code="ENGLISH_IMAGE_PAGE_REVIEW",
                    message="英方课表使用了图片/OCR解析，课程格和跨节占用仍需核对。",
                    severity="warning",
                    source_file=source.file_name,
                    source_hash=source.content_hash,
                    suggestion="在原文核对中检查日期、节次列、合并课程格及全部续页，再确认解析结果。",
                    blocks_export=True,
                )
            )
    raster_clipping_check = getattr(schedule_core, "chinese_image_clipped_pages", None)
    if callable(raster_clipping_check):
        for source, inspection in zip(add_result.added, pdf_inspections):
            if (
                source.kind != "中方"
                or source not in ready_sources
                or not inspection.get("pages_needing_ocr")
                or inspection.get("clipped_pages")
            ):
                continue
            clipped = raster_clipping_check(
                {
                    "path": source.source_path,
                    "bytes_data": source.bytes_data,
                    "name": source.file_name,
                    "content_hash": source.content_hash,
                }
            )
            if clipped:
                inspection_issues.append(
                    ParseIssue(
                        code="PDF_PAGE_CONTENT_CLIPPED",
                        message="图片课表的星期列或底部表格在页面边界被截断，缺失课程不可见。",
                        severity="error",
                        source_file=source.file_name,
                        source_hash=source.content_hash,
                        suggestion="重新导出能完整显示全部星期列和课程行的 PDF。",
                        blocks_export=True,
                    )
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
    # 未通过结构检查的文件也必须出现在“文件检查”明细与统计里：
    # 它们没有进入 ready_sources，否则会从 details/pdf_count 中消失，
    # 而 job_files 里明明还有这一行，用户无法定位是哪个文件失败。
    blocked_records = _blocked_file_records(add_result.added, pdf_inspections)
    if blocked_records:
        generation.file_records.extend(blocked_records)
        process_result.file_records = generation.file_records
        process_result.failed_files = [
            record for record in generation.file_records if record.status == "解析失败"
        ]
        process_result.summary.source_file_count = len(generation.file_records)
        process_result.summary.failed_file_count = len(process_result.failed_files)
    # 解析结果必须落在当前学期范围内：晚于教学周的课程若被静默裁剪，用户会拿到
    # 一张缺课的空课表；早于学期的课程被钳到第 1 周同样不可信。范围检查与修正
    # 路径共用 core.quality.schedule_range_violations，避免两条路径结论相反。
    schedule_range_issues = _schedule_range_issues(generation)
    signature = build_parser_signature()
    quality_state, issues = evaluate_quality(
        file_records=process_result.file_records,
        members=process_result.members,
        inherited_issues=[*preflight_issues, *inspection_issues, *schedule_range_issues],
        enforce_identity=enforce_quality,
    )
    process_result.quality_state = quality_state
    process_result.issues = issues
    process_result.parser_signature = signature.digest
    if progress:
        report_ready_progress(
            "review" if quality_state != "accepted" else "completed",
            len(ready_sources),
            len(ready_sources),
            "课表识别完成，请逐项对照原件核验",
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
    department: str = "",
    week: int | None = None,
) -> Path:
    """Export the parsed availability result to an Excel file."""

    if mode not in {"classic", "qinghe"}:
        raise ValueError("visual 导出模式已预留，等待可视化表格样式后实现。")
    if workflow_result.process_result.parser_signature != build_parser_signature().digest:
        raise ValueError("学期设置或解析规则已变化，请重新生成空课表后导出。")
    assert_export_allowed(
        workflow_result.process_result.quality_state,
        workflow_result.process_result.issues,
    )
    scoped = workflow_result.date_review
    if scoped and (week is not None or export_week_count is not None):
        raise ValueError('本次导出采用建立合表时选择的起止日期。')
    if scoped:
        counts = member_counts(workflow_view(workflow_result))
        if not counts['total'] or counts['pending']:
            raise ValueError(f"还有 {counts['pending']} 位成员待核对，请先确认本次日期范围。")
    pending = 0 if scoped else workflow_verification(workflow_result)['pending']
    if pending:
        raise ValueError(f'还有 {pending} 项课程待人工核验，请对照原件逐项确认后导出。')
    if not isinstance(department, str):
        raise ValueError("请选择一个有效部门。")
    department = department.strip()
    target = Path(target_path)
    if target.suffix.lower() != ".xlsx":
        target = target.with_suffix(".xlsx")
    target.parent.mkdir(parents=True, exist_ok=True)
    generation = workflow_result.generation_result
    export_weeks = _export_weeks(generation.weeks, export_week_count)
    if scoped:
        export_weeks = sorted({int(w) for w in selected_calendar(generation.calendar_df, scoped)['week']})
    elif department or mode == "qinghe" or any(i.get('date_coverage') is not None for i in generation.pdf_inspections):
        export_weeks = sorted(set(export_weeks) & set(generation.weeks))
        if not export_weeks:
            raise ValueError('没有原课表共同覆盖的完整工作周，请补充对应日期的课表。')
    if week is not None:
        if type(week) is not int or week not in export_weeks or week not in generation.weeks:
            raise ValueError("所选周次不在本批可导出的范围内。")
        export_weeks = [week]
    request = ExcelExportRequest(
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
                    "说明": record.display_result,
                }
                for record in workflow_result.process_result.file_records
            ]
        ),
        correction_df=pd.DataFrame(
            [
                {
                    # 内部标识与内容哈希不进导出物：只保留可追溯的短编号。
                    "来源编号": (correction.source_hash or "")[:8],
                    "课程块": (correction.block_id or "")[:8],
                    **correction_export_values(correction),
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
                {"项目": "数据范围", "说明": "仅包含本次导入并通过质量门禁的课表；空课周次：" + '、'.join(map(str, export_weeks)) + '。范围外不代表空闲。'},
                {"项目": "隐私", "说明": "原始 PDF 未复制到数据库或工作簿。"},
                {"项目": "复核", "说明": "问题和人工修正分别记录在对应工作表。"},
            ]
        ),
        weekly_only=mode == "qinghe",
        department=department,
    )
    if department:
        from app.services.department_scope import department_workbook_request
        request = department_workbook_request(request, workflow_result, schedule_core, department)
    elif week is not None:
        if "week" in request.blocks_df.columns:
            request.blocks_df = request.blocks_df[request.blocks_df["week"] == week]
            request.member_course_df = request.blocks_df
        if "week" in request.all_slot_df.columns:
            request.all_slot_df = request.all_slot_df[request.all_slot_df["week"] == week]
    if week is not None:
        request.instructions_df.loc[len(request.instructions_df)] = {
            "项目": "单周范围", "说明": f"排班、空课与课程明细只列第{week}周。名册、文件、问题和修正记录按本次导出的部门或全协会批次列示。"
        }
    if scoped:
        request.start_date, request.end_date = scoped['start_date'], scoped['end_date']
        request.calendar_df = selected_calendar(generation.calendar_df, scoped)
        date_by_slot = {(int(r.week), r.weekday): str(r.date)[:10] for r in request.calendar_df.itertuples()}
        request.occupancy = {key: values for key, values in request.occupancy.items() if key[:2] in date_by_slot}
        for name in ('all_slot_df', 'blocks_df', 'member_course_df'):
            frame = getattr(request, name)
            if not frame.empty:
                mask = [(int(w), d) in date_by_slot for w, d in zip(frame['week'], frame['weekday'])]
                setattr(request, name, frame.loc[mask].copy())
        request.instructions_df.loc[len(request.instructions_df)] = {
            '项目': '人工核对日期', '说明': f"{request.start_date} 至 {request.end_date}，含首尾；成员已逐人核对。"}
        selected_members = set(request.students)
        def review_event_label(event):
            if event['action'] == 'flag':
                return '标记待核实' if event['open'] else '问题已核实'
            if event['action'] == 'slot':
                return '补上有课' if event['marked'] else '取消有课'
            return '确认成员'
        logs = [dict(时间=e['created_at'], 成员=e['member_key'], 操作=review_event_label(e),
                    日期=e.get('date') or '', 节次=e.get('period') or '',
                    来源编号=(e.get('source_hash') or '')[:8], 人工核实缺资料日期='、'.join(e.get('missing_dates', [])))
                for e in scoped['log'] if e['member_key'] in selected_members]
        if logs:
            request.correction_df = pd.concat([request.correction_df, pd.DataFrame(logs)], ignore_index=True)
    data = build_export_excel_bytes(schedule_core=schedule_core, request=request)
    temporary = target.with_name(target.name + ".tmp")
    try:
        # 先写临时文件再原子替换：写失败或目标被 Excel 占用时不能留下半截工作簿，
        # 也不破坏用户原有的同名文件。
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(
            "无法写入导出文件：请关闭正在占用该文件的程序（如 Excel），或换一个位置后重试。"
        ) from exc
    return target


def save_workflow_result(
    workflow_result: DesktopWorkflowResult,
    store: StateStore,
    job_id: str | None = None,
    request: dict[str, Any] | None = None,
    *,
    completed: bool = True,
) -> str:
    """Persist a versioned JSON result under a stable job UUID."""

    result_id = job_id or store.create_job(
        request or {},
        workflow_result.process_result.parser_signature,
        [source.source_path for source in workflow_result.generation_result.model_sources],
    )
    workflow_result.process_result.job_id = result_id
    snapshot = snapshot_workflow(workflow_result)
    file_count = len(workflow_result.generation_result.file_records)
    store.save_job_result(
        result_id,
        result_json=json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        error="",
        quality_state=workflow_result.process_result.quality_state,
        current=file_count,
        total=file_count,
        issues=snapshot.get("issues", []),
        completed=completed,
    )
    return result_id


def load_workflow_result(result_ref: str, store: StateStore, schedule_core: Any) -> DesktopWorkflowResult:
    """Load a JSON workflow snapshot by stable job UUID."""

    job = store.get_job(result_ref)
    if not job or not job.get("result"):
        raise ValueError("解析结果引用无效或来自旧版，请重新解析后导出。")
    return restore_workflow(job["result"], schedule_core)


def serialize_workflow_result(workflow_result: DesktopWorkflowResult, result_ref: str) -> dict[str, Any]:
    signature_current = workflow_result.process_result.parser_signature == build_parser_signature().digest
    warnings = list(workflow_result.warnings)
    if not signature_current:
        warning = "此结果与当前学期设置或解析规则不一致，请重新解析课表并生成空课表后导出。"
        if warning not in warnings:
            warnings.append(warning)
    payload = serialize_process_result(
        result=workflow_result.process_result,
        preview_df=workflow_result.preview_df,
        warnings=warnings,
        errors=workflow_result.errors,
        result_ref=result_ref,
    )
    # Keep the result's historical quality state, but mirror the live export
    # guard so an old accepted snapshot does not advertise export readiness.
    if workflow_result.date_review is not None:
        view = workflow_view(workflow_result)
        payload['date_review'] = view
        verification = member_counts(view)
        if verification['pending'] or not verification['total'] or not signature_current or not payload['can_export']:
            payload['availability'] = []
            payload['availability_preview'] = []
            payload['summary']['availability_slot_count'] = 0
    else:
        verification = workflow_verification(workflow_result)
    payload["department_scopes"] = build_department_scopes(workflow_result, payload["availability_preview"])
    payload["verification"] = {key: verification[key] for key in ("total", "confirmed", "pending")}
    payload["can_export"] = payload["can_export"] and signature_current and not verification["pending"]
    generation = workflow_result.generation_result
    range_codes = {i.code for i in workflow_result.process_result.issues
                   if i.code in {"WEEK_OUT_OF_SEMESTER", "PRE_TERM_DATE"}}
    if range_codes:
        from core.quality import schedule_range_blocks
        frame = generation.calendar_df
        weeks = {int(value) for value in frame.get("week", []) if pd.notna(value)}
        start = min((str(value) for value in frame.get("date", []) if pd.notna(value)), default=None)
        affected = schedule_range_blocks(generation.blocks, weeks, start)
        payload["range_evidence"] = {code: [row["block_id"] for row in affected[code] if row.get("block_id")]
                                     for code in range_codes}
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
        active_issues = [
            issue for issue in workflow_result.process_result.issues
            if issue.blocks_export and not issue.confirmed and (
                issue.source_hash == detail.get("source_hash") if issue.source_hash
                else bool(issue.source_file) and issue.source_file == detail.get("filename")
            )
        ]
        if active_issues:
            detail["quality_state"] = (
                "blocked" if any(issue.severity == "error" for issue in active_issues) else "needs_review"
            )
            detail["warning"] = "；".join(dict.fromkeys(
                str(message) for message in [detail.get("warning"), *[issue.message for issue in active_issues]]
                if message
            ))
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
        if "nonclass_consensus" in inspection:
            detail["nonclass_consensus"] = deepcopy(inspection["nonclass_consensus"])
    return payload


def _notice_consensus_issue(source_file: str, source_hash: str | None, note: dict[str, Any]) -> ParseIssue:
    if note.get('kind') == 'native_crop_offcampus' and not note.get('requires_review', True):
        return ParseIssue(code='LOCAL_LOCATION_OCR_VERIFIED',
            message=f'第 {int(note["page"])+1} 页地点“{note["canonical_label"]}”已通过局部重识别核验，按校外课程规则不计占用。',
            severity='info',source_file=source_file,source_hash=source_hash,
            suggestion='可在原文核对中查看该课程所在的完整行。',blocks_export=False)
    if note.get('kind') == 'native_crop_notice' and not note.get('requires_review', True):
        label = str(note['canonical_label'])
        return ParseIssue(code='LOCAL_NOTICE_OCR_VERIFIED',
            message=f'第 {int(note["page"])+1} 页“{label}”已通过局部重识别核验，为日历说明，不计作课程。',
            severity='info',source_file=source_file,source_hash=source_hash,
            suggestion='可在原文核对中查看该说明所在的完整行。',blocks_export=False)
    original = note.get("original_ocr") or []
    if isinstance(original, list):
        original_text = "、".join(str(item.get("text", "") if isinstance(item, dict) else item) for item in original)
    else:
        original_text = str(original)
    page = int(note["page"]) + 1
    periods = "、".join(str(period) for period in note.get("periods", [])) or "待定位"
    location = f"第{page}页，第{note.get('week', '待定位')}周{note.get('weekday', '')}第{periods}节"
    label = str(note.get("canonical_label") or "非课标记")
    evidence_id = hashlib.sha256(json.dumps(note, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    excluded = note.get('action') == 'excluded_from_preview_occupancy'
    message = (f"{location}：原OCR“{original_text or '未提取到文字'}”按同页规范字形判断为“{label}”；预览已按非课程排除，请核对原页。"
               if excluded else f"{location}：原OCR“{original_text or '未提取到文字'}”与同页规范标记字形相似，疑似“{label}”；预览仍保留原占用，请核对原页。")
    suggestion = (f"请对照同页两处规范“{label}”标记核对上述周次与节次；若判断有误，在原文核对中选择该区域并使用“恢复为课程并重算”；尚未处理时保留待复核。"
                  if excluded else "确认此提示只记录核对，不会自动排除原占用。若确为非课程，在原文核对中选择该区域并使用“排除并重算”；尚未处理时保留待复核。若确为课程，可确认此提示。")
    return ParseIssue(
        code="VISUAL_NOTICE_CONSENSUS_REVIEW",
        message=message,
        severity="warning",
        source_file=source_file,
        source_hash=source_hash,
        field=f"nonclass_consensus:{evidence_id}",
        suggestion=suggestion,
        blocks_export=True,
        confirmed=False,
    )


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


def _blocked_file_records(
    sources: list[Any],
    pdf_inspections: list[dict[str, Any]],
) -> list[FileProcessRecord]:
    """Build failed records for PDFs that never reached the parser."""

    records: list[FileProcessRecord] = []
    for source, inspection in zip(sources, pdf_inspections):
        if inspection.get("status") == "ready":
            continue
        message = str(inspection.get("error") or "PDF 结构检查未通过，已阻止继续解析。")
        records.append(
            FileProcessRecord(
                source=source,
                status="解析失败",
                member_name=infer_member_name_from_filename(source.file_name) or None,
                detected_kind=source.kind,
                text_source="未知",
                block_count=0,
                result_message=f"未通过 PDF 结构检查：{message}",
                error=ProcessError(
                    error_type=ErrorType.FILE_READ_FAILED,
                    message=message,
                    source_file=source.file_name,
                ),
                source_hash=source.content_hash,
                quality_state="blocked",
            )
        )
    return records


def _detected_weeks(blocks: list[dict[str, Any]]) -> list[int]:
    weeks: set[int] = set()
    for block in blocks:
        try:
            weeks.add(int(block.get("week")))
        except Exception:
            continue
    return sorted(weeks)


def _schedule_range_issues(generation: Any) -> list[ParseIssue]:
    """Build out-of-term issues from the parsed blocks and the active calendar."""

    calendar_weeks: set[int] = set()
    calendar_dates: list[str] = []
    frame = generation.calendar_df
    if frame is not None and not frame.empty:
        if "week" in frame:
            for value in frame["week"]:
                try:
                    calendar_weeks.add(int(value))
                except (TypeError, ValueError):
                    continue
        if "date" in frame:
            calendar_dates = [str(value) for value in frame["date"] if str(value).strip()]
    problems = schedule_range_violations(
        blocks=generation.blocks,
        calendar_weeks=calendar_weeks,
        earliest_calendar_date=min(calendar_dates) if calendar_dates else None,
    )
    return [
        ParseIssue(
            code=code,
            message=message,
            severity="error",
            suggestion=SCHEDULE_RANGE_SUGGESTION,
            blocks_export=True,
        )
        for code, message in problems
    ]


def _export_weeks(default_weeks: list[int], export_week_count: int | None) -> list[int]:
    parsed = {int(week) for week in default_weeks if week is not None}
    if export_week_count is None:
        return sorted(parsed)
    try:
        count = int(export_week_count)
    except Exception:
        return sorted(parsed)
    if count <= 0:
        return sorted(parsed)
    # The requested week count must never drop weeks the parser actually found:
    # exporting week 1..count only would silently remove later teaching weeks.
    weeks = set(range(1, min(count, 30) + 1)) | parsed
    return sorted(week for week in weeks if 1 <= week <= 30)


def _selected_positions(pdf_paths: list[str], ready_sources: list[Any]) -> list[int]:
    """Map each ready PdfSource back to its 0-based index in the selected paths."""

    positions: dict[str, int] = {}
    for index, path in enumerate(pdf_paths):
        positions.setdefault(_resolved_path_key(path), index)
    by_name: dict[str, int] = {}
    for index, path in enumerate(pdf_paths):
        by_name.setdefault(Path(path).name, index)
    mapped: list[int] = []
    for source in ready_sources:
        position = positions.get(_resolved_path_key(str(getattr(source, "source_path", ""))))
        if position is None:
            position = by_name.get(Path(str(getattr(source, "file_name", ""))).name)
        mapped.append(position if position is not None else 0)
    return mapped


def _resolved_path_key(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        return str(Path(path))


def _selected_index(current: int, ready_positions: list[int], message: str = "") -> int:
    """Clamp a parser progress value onto the selected-path index space.

    The recovered parser core reports completion counts 1-based ("已完成 2/5
    份课表", "{file} 已阻止处理") while its mid-file OCR page events carry the
    0-based index of the file being processed.  Both are normalised here so
    callers always receive a 0-based index into the originally selected paths.
    """

    if not ready_positions:
        return 0
    try:
        value = int(current)
    except (TypeError, ValueError):
        value = 0
    if "图片文字识别" in message:
        index = min(max(value, 0), len(ready_positions) - 1)
    else:
        index = min(max(value - 1, 0), len(ready_positions) - 1)
    return ready_positions[index]

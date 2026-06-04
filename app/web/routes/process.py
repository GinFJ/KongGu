"""Processing endpoints for the Konggu local Web UI."""

from __future__ import annotations

import logging
import threading

import pandas as pd
from fastapi import APIRouter, HTTPException

from app.bootstrap import load_schedule_core
from app.services.availability_preview_service import build_availability_preview
from app.services.generate_availability_service import generate_availability
from app.services.result_view_service import build_gui_process_result
from app.web.schemas import acceptance_summary, file_record_has_warning, result_summary, serialize_log
from app.web.task_manager import TASK_NOT_FOUND_MESSAGE, task_manager


router = APIRouter(prefix="/api/process", tags=["process"])
schedule_app = load_schedule_core()
LOGGER = logging.getLogger("konggu.web")
WEEKDAYS = getattr(schedule_app, "WEEKDAYS", ["周一", "周二", "周三", "周四", "周五", "周六", "周日"])


def _load_default_timetable() -> pd.DataFrame:
    try:
        timetable = schedule_app.default_timetable()
        timetable, _ = schedule_app.validate_timetable(timetable)
        return timetable
    except Exception:
        return pd.DataFrame([{"period": i, "start": "", "end": ""} for i in range(1, 12)])


def _collect_result_warnings(result, generation_errors: list[str]) -> list[str]:
    warnings = list(generation_errors or [])
    for record in result.file_records:
        if file_record_has_warning(record):
            message = record.display_result or "未识别到有效课程块"
            warnings.append(f"{record.source.file_name}: {message}")
    return list(dict.fromkeys(str(warning) for warning in warnings if warning))


def _worker(task_id: str) -> None:
    try:
        task = task_manager.require(task_id)
    except KeyError:
        return

    try:
        first_file = task.uploaded_files[0].filename if task.uploaded_files else ""
        task_manager.set_status(task_id, "RUNNING")
        task_manager.set_progress(task_id, 0.22, stage="正在解析课表 PDF", current_file=first_file)
        task_manager.add_log(task_id, "INFO", "开始解析课表 PDF。")

        generation = generate_availability(
            schedule_core=schedule_app,
            selected_sources=list(task.pdf_sources),
            root_dir="",
            weekdays=WEEKDAYS,
        )
        task_manager.update(task_id, generation_result=generation)
        task_manager.set_progress(task_id, 0.72, stage="正在生成空课表预览")
        task_manager.add_log(task_id, "INFO", "课表解析完成，正在生成空课表预览。")

        preview = build_availability_preview(
            occupancy=generation.occupancy,
            students=generation.students,
            weeks=generation.weeks,
            weekdays=WEEKDAYS,
            calendar_df=generation.calendar_df,
            timetable_df=_load_default_timetable(),
        )
        result = build_gui_process_result(
            course_blocks=generation.course_blocks,
            members=generation.member_schedules,
            file_records=generation.file_records,
            slots=preview.slots,
            student_count=len(generation.students),
            block_count=len(generation.blocks),
            week_count=len(generation.weeks),
            calendar_df=generation.calendar_df,
        )

        result_warnings = _collect_result_warnings(result, list(generation.errors or []))
        failed_file_count = sum(1 for record in result.file_records if file_record_has_warning(record))
        all_files_failed = bool(result.file_records) and failed_file_count >= len(result.file_records)
        has_usable_result = bool(result.blocks or result.slots or result.members)

        if all_files_failed and not has_usable_result:
            for warning in result_warnings:
                task_manager.add_log(task_id, "ERROR", warning)
            task_manager.set_failed(task_id, "所有 PDF 均解析失败，请检查文件内容或重新上传。")
            return

        task = task_manager.require(task_id)
        combined_warnings = list(dict.fromkeys([*task.warnings, *result_warnings]))
        summary = result_summary(result, pdf_count=len(task.uploaded_files), warning_count=len(combined_warnings) + len(task.errors))
        summary["acceptance"] = acceptance_summary(
            result=result,
            uploaded_count=len(task.uploaded_files),
            warnings=combined_warnings,
            errors=task.errors,
        )

        task_manager.update(task_id, warnings=combined_warnings)
        for warning in result_warnings:
            task_manager.add_log(task_id, "WARNING", warning)
        task_manager.set_result(
            task_id,
            result=result,
            generation_result=generation,
            preview_df=preview.free_df,
            summary=summary,
        )
        if combined_warnings:
            task_manager.add_log(
                task_id,
                "SUCCESS",
                f"处理完成，有 {len(combined_warnings)} 条警告。可先查看成员检查和识别明细。",
            )
        else:
            task_manager.add_log(
                task_id,
                "SUCCESS",
                f"处理完成：{len(generation.students)} 名成员，{len(preview.slots)} 个空课表时段。",
            )
    except Exception as exc:
        LOGGER.exception("Web processing failed for task_id=%s", task_id)
        task_manager.set_failed(task_id, f"解析失败：{exc}")


@router.post("/start")
async def start_process(payload: dict) -> dict:
    task_id = str(payload.get("task_id", "")).strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id。")
    try:
        task = task_manager.require(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND_MESSAGE) from None

    if task.status == "RUNNING":
        raise HTTPException(status_code=409, detail="任务正在处理中，请勿重复提交。")
    if not task.pdf_sources:
        raise HTTPException(status_code=400, detail="请先上传课表 PDF。")

    thread = threading.Thread(target=_worker, args=(task_id,), daemon=True)
    thread.start()
    return {"ok": True, "message": "processing started"}


@router.get("/status/{task_id}")
async def get_status(task_id: str) -> dict:
    try:
        task = task_manager.require(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND_MESSAGE) from None

    summary = task.summary or result_summary(
        task.result,
        pdf_count=len(task.uploaded_files),
        warning_count=len(task.warnings) + len(task.errors),
    )
    return {
        "ok": True,
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "current_stage": task.current_stage,
        "current_file": task.current_file,
        "logs": [serialize_log(log) for log in task.logs[-100:]],
        "summary": summary,
        "acceptance_summary": summary.get("acceptance")
        or acceptance_summary(
            result=task.result,
            uploaded_count=len(task.uploaded_files),
            warnings=task.warnings,
            errors=task.errors,
        ),
        "warnings": task.warnings,
        "errors": task.errors,
    }


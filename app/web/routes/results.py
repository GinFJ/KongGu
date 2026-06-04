"""Result endpoints for the Konggu local Web UI."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.web.schemas import (
    acceptance_summary,
    dataframe_records,
    result_summary,
    serialize_file_record,
    serialize_log,
    serialize_member,
    serialize_slot,
)
from app.web.task_manager import TASK_NOT_FOUND_MESSAGE, task_manager


router = APIRouter(prefix="/api/results", tags=["results"])


@router.get("/{task_id}")
async def get_results(task_id: str) -> dict:
    try:
        task = task_manager.require(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND_MESSAGE) from None

    if task.result is None:
        summary = task.summary or result_summary(None, pdf_count=len(task.uploaded_files))
        return {
            "ok": True,
            "status": task.status,
            "summary": summary,
            "acceptance_summary": acceptance_summary(
                result=None,
                uploaded_count=len(task.uploaded_files),
                warnings=task.warnings,
                errors=task.errors,
            ),
            "availability": [],
            "members": [],
            "details": [],
            "logs": [serialize_log(log) for log in task.logs],
            "warnings": task.warnings,
            "errors": task.errors,
        }

    summary = task.summary or result_summary(
        task.result,
        pdf_count=len(task.uploaded_files),
        warning_count=len(task.warnings) + len(task.errors),
    )
    return {
        "ok": True,
        "status": task.status,
        "summary": summary,
        "acceptance_summary": summary.get("acceptance")
        or acceptance_summary(
            result=task.result,
            uploaded_count=len(task.uploaded_files),
            warnings=task.warnings,
            errors=task.errors,
        ),
        "availability": [serialize_slot(slot) for slot in task.result.slots],
        "availability_preview": dataframe_records(task.preview_df),
        "members": [serialize_member(member) for member in task.result.members],
        "details": [serialize_file_record(record) for record in task.result.file_records],
        "logs": [serialize_log(log) for log in task.logs],
        "warnings": task.warnings,
        "errors": task.errors,
    }

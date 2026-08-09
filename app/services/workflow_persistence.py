"""Versioned JSON snapshots for desktop workflow results."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd

from app.services.availability_preview_service import build_availability_preview
from app.services.desktop_serialization import json_safe
from app.services.generate_availability_service import AvailabilityGenerationResult
from app.services.result_view_service import build_gui_process_result
from core.legacy_adapter import build_file_records, build_member_schedules, course_blocks_from_legacy
from core.models import CorrectionRecord, ParseIssue, PdfSource


SNAPSHOT_VERSION = 1
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def snapshot_workflow(workflow_result: Any) -> dict[str, Any]:
    generation = workflow_result.generation_result
    process = workflow_result.process_result
    return json_safe(
        {
            "version": SNAPSHOT_VERSION,
            "generation": {
                "sources": [
                    {
                        "file_name": source.file_name,
                        "kind": source.kind,
                        "source_path": source.source_path,
                        "content_hash": source.content_hash,
                    }
                    for source in generation.model_sources
                ],
                "blocks": generation.blocks,
                "calendar_rows": generation.calendar_df.to_dict("records"),
                "occupancy": [
                    {
                        "week": int(key[0]),
                        "weekday": str(key[1]),
                        "period": int(key[2]),
                        "members": sorted(value),
                    }
                    for key, value in generation.occupancy.items()
                ],
                "students": generation.students,
                "weeks": generation.weeks,
                "errors": generation.errors,
                "elapsed_seconds": generation.elapsed_seconds,
                "pdf_inspections": generation.pdf_inspections,
            },
            "quality_state": process.quality_state,
            "issues": [asdict(issue) for issue in process.issues],
            "corrections": [asdict(correction) for correction in process.corrections],
            "parser_signature": process.parser_signature,
            "warnings": workflow_result.warnings,
            "errors": workflow_result.errors,
        }
    )


def restore_workflow(snapshot: dict[str, Any], schedule_core: Any) -> Any:
    if int(snapshot.get("version") or 0) != SNAPSHOT_VERSION:
        raise ValueError("解析结果版本不兼容，请重新解析。")

    from app.services.desktop_workflow import DesktopWorkflowResult

    data = dict(snapshot.get("generation") or {})
    sources = [
        PdfSource(
            file_name=str(item.get("file_name") or ""),
            kind=item.get("kind") if item.get("kind") in {"中方", "英方"} else "中方",
            source_path=str(item.get("source_path") or ""),
            content_hash=str(item.get("content_hash") or "") or None,
        )
        for item in data.get("sources", [])
    ]
    blocks = [dict(block) for block in data.get("blocks", [])]
    calendar_df = pd.DataFrame(data.get("calendar_rows", []))
    occupancy = {
        (int(item["week"]), str(item["weekday"]), int(item["period"])): set(item.get("members", []))
        for item in data.get("occupancy", [])
    }
    students = [str(item) for item in data.get("students", [])]
    weeks = [int(item) for item in data.get("weeks", [])]
    timetable = schedule_core.default_timetable()
    timetable, _ = schedule_core.validate_timetable(timetable)
    periods = [int(period) for period in timetable["period"]]
    all_slot_df = schedule_core.build_slot_table(occupancy, students, weeks, WEEKDAYS, periods)
    blocks_df = schedule_core.blocks_to_dataframe(blocks)
    preview = build_availability_preview(
        occupancy=occupancy,
        students=students,
        weeks=weeks,
        weekdays=WEEKDAYS,
        calendar_df=calendar_df,
        timetable_df=timetable,
    )
    course_blocks = course_blocks_from_legacy(blocks)
    members = build_member_schedules(course_blocks, students)
    file_records = build_file_records(sources, course_blocks, list(data.get("errors", [])))
    generation = AvailabilityGenerationResult(
        model_sources=sources,
        blocks=blocks,
        course_blocks=course_blocks,
        calendar_df=calendar_df,
        occupancy=occupancy,
        students=students,
        weeks=weeks,
        blocks_df=blocks_df,
        all_slot_df=all_slot_df,
        errors=list(data.get("errors", [])),
        preview_df=pd.DataFrame(blocks),
        member_schedules=members,
        file_records=file_records,
        elapsed_seconds=float(data.get("elapsed_seconds") or 0),
        pdf_inspections=[dict(item) for item in data.get("pdf_inspections", [])],
    )
    process = build_gui_process_result(
        course_blocks=course_blocks,
        members=members,
        file_records=file_records,
        slots=preview.slots,
        student_count=len(students),
        block_count=len(blocks),
        week_count=len(weeks),
        calendar_df=calendar_df,
    )
    process.quality_state = snapshot.get("quality_state") or "blocked"
    process.issues = [ParseIssue(**item) for item in snapshot.get("issues", [])]
    process.corrections = [CorrectionRecord(**item) for item in snapshot.get("corrections", [])]
    process.parser_signature = str(snapshot.get("parser_signature") or "")
    return DesktopWorkflowResult(
        generation_result=generation,
        process_result=process,
        preview_df=preview.free_df,
        warnings=list(snapshot.get("warnings", [])),
        errors=list(snapshot.get("errors", [])),
    )

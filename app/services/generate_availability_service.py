"""Application service for parsing schedules and preparing availability data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from core.legacy_adapter import (
    build_file_records,
    build_member_schedules,
    course_blocks_from_legacy,
    legacy_source_to_model,
    model_sources_to_legacy,
)
from core.models import CourseBlock, FileProcessRecord, MemberSchedule, PdfSource


@dataclass(slots=True)
class AvailabilityGenerationResult:
    """Parsed schedule data prepared for GUI display and export."""

    model_sources: list[PdfSource]
    blocks: list[dict[str, Any]]
    course_blocks: list[CourseBlock]
    calendar_df: pd.DataFrame
    occupancy: dict
    students: list[str]
    weeks: list[int]
    blocks_df: pd.DataFrame
    all_slot_df: pd.DataFrame
    errors: list[str]
    preview_df: pd.DataFrame
    member_schedules: list[MemberSchedule]
    file_records: list[FileProcessRecord]
    elapsed_seconds: float


def generate_availability(
    *,
    schedule_core,
    selected_sources: list[PdfSource],
    root_dir: str,
    weekdays: list[str],
) -> AvailabilityGenerationResult:
    """Parse selected or directory-discovered PDFs and build availability tables."""

    started_at = pd.Timestamp.now()
    model_sources = list(selected_sources)
    root = root_dir.strip()
    if not model_sources and root:
        legacy_dir_sources = schedule_core.local_pdf_sources_from_dir(root)
        model_sources.extend(legacy_source_to_model(source) for source in legacy_dir_sources)

    if not model_sources:
        raise ValueError("请先添加课表 PDF，或选择一个包含课表 PDF 的本地目录。")

    legacy_sources = model_sources_to_legacy(model_sources)
    blocks, calendar_df, errors, preview_df = schedule_core.parse_actual_pdf_sources(
        legacy_sources,
        uploaded_calendar_df=None,
    )

    if calendar_df is None or calendar_df.empty:
        calendar_df = schedule_core.synthesize_calendar_from_blocks(blocks)

    if not blocks:
        raise ValueError("没有识别到可用课表时间块。请确认 PDF 清晰，且文件类型选择为中方或英方。")

    course_blocks = course_blocks_from_legacy(blocks)
    occupancy = schedule_core.build_occupancy(blocks)
    students = sorted({block["name"] for block in blocks})
    weeks = sorted({int(block["week"]) for block in blocks if block.get("week") is not None})
    periods = list(range(1, 12))
    all_slot_df = schedule_core.build_slot_table(occupancy, students, weeks, weekdays, periods)
    blocks_df = schedule_core.blocks_to_dataframe(blocks)
    member_schedules = build_member_schedules(course_blocks, students)
    file_records = build_file_records(model_sources, course_blocks, errors or [])
    elapsed = (pd.Timestamp.now() - started_at).total_seconds()

    return AvailabilityGenerationResult(
        model_sources=model_sources,
        blocks=blocks,
        course_blocks=course_blocks,
        calendar_df=calendar_df,
        occupancy=occupancy,
        students=students,
        weeks=weeks,
        blocks_df=blocks_df,
        all_slot_df=all_slot_df,
        errors=errors or [],
        preview_df=preview_df,
        member_schedules=member_schedules,
        file_records=file_records,
        elapsed_seconds=elapsed,
    )

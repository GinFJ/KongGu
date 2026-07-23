"""Application service for building exported availability Excel files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(slots=True)
class ExcelExportRequest:
    """Data needed to produce the final empty-schedule workbook."""

    occupancy: dict
    students: list[str]
    weeks: list[int]
    calendar_df: pd.DataFrame
    timetable_df: pd.DataFrame
    blocks_df: pd.DataFrame
    all_slot_df: pd.DataFrame
    threshold: int = 0
    member_course_df: pd.DataFrame | None = None
    issue_df: pd.DataFrame | None = None
    file_df: pd.DataFrame | None = None
    correction_df: pd.DataFrame | None = None
    instructions_df: pd.DataFrame | None = None


def build_export_excel_bytes(*, schedule_core: Any, request: ExcelExportRequest) -> bytes:
    """Build the Excel workbook bytes for the generated availability table."""

    return schedule_core.build_empty_schedule_excel_bytes(
        occupancy=request.occupancy,
        students=request.students,
        weeks=request.weeks,
        calendar_df=request.calendar_df,
        timetable_df=request.timetable_df,
        threshold=request.threshold,
        blocks_df=request.blocks_df,
        all_slot_df=request.all_slot_df,
        member_course_df=request.member_course_df,
        issue_df=request.issue_df,
        file_df=request.file_df,
        correction_df=request.correction_df,
        instructions_df=request.instructions_df,
    )

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
    )

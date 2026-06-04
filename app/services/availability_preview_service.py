"""Service for building the GUI availability preview table."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.legacy_adapter import availability_slot_from_row
from core.models import AvailabilitySlot


@dataclass(slots=True)
class AvailabilityPreviewResult:
    """Rows and structured slots shown in the empty-schedule tab."""

    free_df: pd.DataFrame
    slots: list[AvailabilitySlot]


def build_availability_preview(
    *,
    occupancy: dict,
    students: list[str],
    weeks: list[int],
    weekdays: list[str],
    calendar_df: pd.DataFrame,
    timetable_df: pd.DataFrame,
) -> AvailabilityPreviewResult:
    """Build the full availability preview for all weeks, weekdays, and 1-11 periods."""

    if not students or not weeks:
        return AvailabilityPreviewResult(free_df=pd.DataFrame(), slots=[])

    total_students = len(students)
    rows = []
    slots = []
    date_map = build_date_map(calendar_df)
    time_map = build_time_map(timetable_df)

    for week in weeks:
        for weekday in weekdays:
            for period in range(1, 12):
                busy_students = sorted(occupancy.get((week, weekday, period), set()))
                free_students = [student for student in students if student not in busy_students]
                free_count = total_students - len(busy_students)
                row = {
                    "周次": week,
                    "日期": date_map.get((week, weekday), ""),
                    "星期": weekday,
                    "节次": period,
                    "时间": time_map.get(period, ""),
                    "空闲人数": free_count,
                    "空闲人员": "、".join(free_students),
                    "占用人数": len(busy_students),
                    "有课人员": "、".join(busy_students),
                }
                rows.append(row)
                slots.append(availability_slot_from_row(row))

    return AvailabilityPreviewResult(free_df=pd.DataFrame(rows), slots=slots)


def build_date_map(calendar_df: pd.DataFrame) -> dict[tuple[int, str], str]:
    """Build a (week, weekday) -> date string lookup from a calendar DataFrame."""

    if calendar_df is None or calendar_df.empty:
        return {}

    df = calendar_df.copy()
    if "date" not in df.columns or "week" not in df.columns or "weekday" not in df.columns:
        return {}

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    mapping = {}
    for _, row in df.dropna(subset=["date"]).iterrows():
        try:
            week = int(row["week"])
        except Exception:
            continue
        weekday = str(row["weekday"])
        mapping[(week, weekday)] = row["date"].strftime("%Y-%m-%d")
    return mapping


def build_time_map(timetable_df: pd.DataFrame) -> dict[int, str]:
    """Build a period -> time range lookup from a timetable DataFrame."""

    if timetable_df is None or timetable_df.empty:
        return {}

    mapping = {}
    for _, row in timetable_df.iterrows():
        try:
            period = int(row["period"])
        except Exception:
            continue
        start = str(row.get("start", "")).strip()
        end = str(row.get("end", "")).strip()
        mapping[period] = f"{start}-{end}" if start or end else ""
    return mapping

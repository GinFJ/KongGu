"""Service for building the GUI availability preview table."""

from __future__ import annotations

from dataclasses import dataclass
import re

import pandas as pd

from core.legacy_adapter import availability_slot_from_row
from core.models import AvailabilitySlot


def display_member_name(value: object) -> str:
    """Return the user-facing name of an internal ``姓名｜部门｜角色`` member key."""

    text = str(value or "").strip()
    if not text:
        return ""
    name = re.split(r"\s*[｜|]\s*", text, maxsplit=1)[0].strip()
    return name or text


def build_display_name_map(students: list[str]) -> dict[str, str]:
    """Map internal member keys to display names, keeping keys that would collide."""

    keys = [str(student) for student in students]
    names = {key: display_member_name(key) for key in keys}
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    return {key: (key if counts[display] > 1 else display) for key, display in names.items()}


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
    """Build the full availability preview for all weeks, weekdays, and configured periods."""

    if not students or not weeks:
        return AvailabilityPreviewResult(free_df=pd.DataFrame(), slots=[])

    total_students = len(students)
    display_names = build_display_name_map(students)
    rows = []
    slots = []
    date_map = build_date_map(calendar_df)
    time_map = build_time_map(timetable_df)
    periods = build_period_order(timetable_df) or list(range(1, 12))

    def label(member: str) -> str:
        return display_names.get(member) or display_member_name(member)

    labels = {student: label(student) for student in students}
    for week in weeks:
        for weekday in weekdays:
            for period in periods:
                occupied = occupancy.get((week, weekday, period), set())
                busy_students = sorted(occupied)
                free_students = [student for student in students if student not in occupied]
                free_count = total_students - len(busy_students)
                row = {
                    "周次": week,
                    "日期": date_map.get((week, weekday), ""),
                    "星期": weekday,
                    "节次": period,
                    "时间": time_map.get(period, ""),
                    "空闲人数": free_count,
                    "空闲人员": "、".join(labels[student] for student in free_students),
                    "占用人数": len(busy_students),
                    "有课人员": "、".join(labels[student] if student in labels else label(student) for student in busy_students),
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


def align_slot_dates(all_slot_df: pd.DataFrame, calendar_df: pd.DataFrame) -> pd.DataFrame:
    """Use the parsed calendar for detail rows, just as the preview does."""

    if all_slot_df.empty or not {"week", "weekday", "date"}.issubset(all_slot_df.columns):
        return all_slot_df
    date_map = build_date_map(calendar_df)
    aligned = all_slot_df.copy()
    aligned["date"] = [
        date_map.get((int(row.week), str(row.weekday)), "")
        for row in aligned.itertuples(index=False)
    ]
    return aligned


def build_period_order(timetable_df: pd.DataFrame) -> list[int]:
    """Return periods in the timetable's display order."""

    if timetable_df is None or timetable_df.empty or "period" not in timetable_df.columns:
        return []

    periods: list[int] = []
    for _, row in timetable_df.iterrows():
        try:
            period = int(row["period"])
        except Exception:
            continue
        if period not in periods:
            periods.append(period)
    return periods


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

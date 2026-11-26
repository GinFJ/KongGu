from datetime import date, timedelta
from io import BytesIO

import pandas as pd
import pytest
from openpyxl import load_workbook

from app.services.export_excel_service import ExcelExportRequest, build_export_excel_bytes
from core import schedule_core as core


def batch_request():
    monday = date(2026, 11, 23)
    calendar = pd.DataFrame([
        {"date": (monday + timedelta(days=i)).isoformat(), "week": 13 + i // 7, "weekday": core.WEEKDAYS[i % 7]}
        for i in range(14)
    ])
    slots = []
    for day in calendar.to_dict("records"):
        for period in core.default_timetable().period:
            busy = day["date"] == "2026-11-28" and period == 2
            slots.append({**day, "period": period, "free_members": "乙" if busy else "甲、乙",
                          "occupied_members": "甲" if busy else "", "free_count": 1 if busy else 2,
                          "occupied_count": 1 if busy else 0})
    blocks = calendar.iloc[[0, 5, 8, 12]].assign(name="甲", period=2, course="示例课程")
    return ExcelExportRequest(
        occupancy={(13, "周六", 2): {"甲"}}, students=["甲", "乙"], weeks=[13, 14],
        calendar_df=calendar, timetable_df=core.default_timetable(), blocks_df=blocks,
        all_slot_df=pd.DataFrame(slots), member_course_df=blocks, start_date="2026-11-27", end_date="2026-12-02",
    )


def export(request):
    return load_workbook(BytesIO(build_export_excel_bytes(schedule_core=core, request=request)))


def rows(sheet):
    columns, *values = sheet.values
    return [dict(zip(columns, row)) for row in values]


@pytest.mark.parametrize("weekly_only", [True, False])
def test_confirmed_range_preserves_dates_and_weekends_in_each_mode(weekly_only):
    request = batch_request()
    request.weekly_only = weekly_only
    workbook = export(request)
    first, second = workbook["第13周"], workbook["第14周"]
    assert [first.cell(3, c).value.date().isoformat() for c in [2, 6, 10]] == [
        "2026-11-27", "2026-11-28", "2026-11-29"]
    assert [second.cell(3, c).value.date().isoformat() for c in [2, 6, 10]] == [
        "2026-11-30", "2026-12-01", "2026-12-02"]
    assert first.max_column == second.max_column == 13
    assert "周六" in first["F3"].number_format
    assert first["F5"].value == "乙"
    assert "2026-11-27" in first["A1"].value
    if weekly_only:
        assert workbook.sheetnames == ["第13周", "第14周"]
        return
    for title in ("空课明细", "全部空课明细"):
        detail = rows(workbook[title])
        assert len(detail) == 6 * len(core.default_timetable())
        assert min(r["日期"] for r in detail) == request.start_date
        assert max(r["日期"] for r in detail) == request.end_date
        assert next(r for r in detail if r["日期"] == "2026-11-28" and r["节次"] == 2)["空闲人员"] == "乙"
    for title in ("课程占用明细", "成员课程明细"):
        assert [r["日期"] for r in rows(workbook[title])] == ["2026-11-28", "2026-12-01"]


def test_one_day_with_complete_records_and_no_busy_students():
    request = batch_request()
    request.start_date = request.end_date = "2026-11-29"
    request.blocks_df = pd.DataFrame()
    book = export(request)
    assert "第14周" not in book and book["第13周"].max_column == 5
    assert book["第13周"]["B3"].value.date() == date(2026, 11, 29)
    assert book["第13周"]["B5"].value == "甲，乙"


@pytest.mark.parametrize("missing", ["date", "period", "calendar_end", "one_bound", "invalid_bound"])
def test_range_rejects_missing_or_invalid_data(missing):
    request = batch_request()
    if missing == "date":
        request.all_slot_df = request.all_slot_df[request.all_slot_df.date != "2026-11-29"]
    elif missing == "period":
        request.all_slot_df = request.all_slot_df[~((request.all_slot_df.date == "2026-11-29") &
                                                   (request.all_slot_df.period == 3))]
    elif missing == "calendar_end":
        request.calendar_df = request.calendar_df[request.calendar_df.date != request.end_date]
    elif missing == "one_bound":
        request.start_date = None
    else:
        request.end_date = "2026-11-01"
    with pytest.raises(ValueError):
        export(request)

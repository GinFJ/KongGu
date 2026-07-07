import pandas as pd

from app.services.availability_preview_service import build_availability_preview, build_date_map, build_period_order, build_time_map


def test_build_availability_preview_reports_free_and_busy_members():
    calendar_df = pd.DataFrame([{"date": "2026-03-16", "week": 3, "weekday": "周一"}])
    timetable_df = pd.DataFrame([{"period": 1, "start": "08:10", "end": "08:55"}])

    result = build_availability_preview(
        occupancy={(3, "周一", 1): {"张三"}},
        students=["张三", "李四"],
        weeks=[3],
        weekdays=["周一"],
        calendar_df=calendar_df,
        timetable_df=timetable_df,
    )

    assert len(result.free_df) == 1
    first = result.free_df.iloc[0]
    assert first["周次"] == 3
    assert first["日期"] == "2026-03-16"
    assert first["星期"] == "周一"
    assert first["节次"] == 1
    assert first["时间"] == "08:10-08:55"
    assert first["空闲人数"] == 1
    assert first["空闲人员"] == "李四"
    assert first["占用人数"] == 1
    assert first["有课人员"] == "张三"
    assert result.slots[0].free_members == ["李四"]
    assert result.slots[0].busy_members == ["张三"]


def test_build_availability_preview_returns_empty_result_without_members_or_weeks():
    result = build_availability_preview(
        occupancy={},
        students=[],
        weeks=[],
        weekdays=["周一"],
        calendar_df=pd.DataFrame(),
        timetable_df=pd.DataFrame(),
    )

    assert result.free_df.empty
    assert result.slots == []


def test_build_date_and_time_maps_skip_invalid_rows():
    calendar_df = pd.DataFrame(
        [
            {"date": "2026-03-16", "week": "3", "weekday": "周一"},
            {"date": "not-a-date", "week": "4", "weekday": "周二"},
            {"date": "2026-03-18", "week": "bad", "weekday": "周三"},
        ]
    )
    timetable_df = pd.DataFrame(
        [
            {"period": "1", "start": "08:10", "end": "08:55"},
            {"period": "bad", "start": "09:00", "end": "09:45"},
        ]
    )

    assert build_date_map(calendar_df) == {(3, "周一"): "2026-03-16"}
    assert build_time_map(timetable_df) == {1: "08:10-08:55"}
    assert build_period_order(timetable_df) == [1]

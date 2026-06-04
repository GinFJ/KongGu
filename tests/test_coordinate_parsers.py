from core import schedule_core


def test_default_timetable_uses_simple_cdut_schedule():
    timetable = schedule_core.default_timetable().set_index("period")

    assert f"{timetable.loc[1, 'start']}-{timetable.loc[1, 'end']}" == "08:10-08:55"
    assert f"{timetable.loc[5, 'start']}-{timetable.loc[5, 'end']}" == "14:30-15:15"
    assert f"{timetable.loc[6, 'start']}-{timetable.loc[6, 'end']}" == "15:20-16:05"
    assert f"{timetable.loc[11, 'start']}-{timetable.loc[11, 'end']}" == "20:50-21:35"


def test_noon_english_periods_do_not_occupy_afternoon_slots():
    weekday = schedule_core.WEEKDAYS[0]
    student = "student"
    occupancy = {(3, weekday, 12): {student}, (3, weekday, 13): {student}}

    assert schedule_core.period_time_range(12) == "12:40-13:25"
    table = schedule_core.build_slot_table(occupancy, [student], [3], [weekday], [5, 6])

    assert table["time"].tolist() == ["14:30-15:15", "15:20-16:05"]
    assert table["occupied_count"].tolist() == [0, 0]


def _item(text, x, y, w=18, h=8):
    return {"text": text, "x0": float(x), "y0": float(y), "x1": float(x + w), "y1": float(y + h)}


def test_chinese_table_parser_maps_courses_by_xy_cells():
    labels = ["1-2", "3-4", "午", "5-6", "7-8", "9-11"]
    items = []
    x = 100
    for _weekday in schedule_core.WEEKDAYS[:5]:
        for label in labels:
            items.append(_item(label, x, 48, w=12))
            x += 32

    items.extend(
        [
            _item("3周", 20, 100, w=20),
            _item("03/16", 48, 100, w=28),
            _item("高等数学", 102, 101, w=36),
            _item("无", 134, 101, w=8),
            _item("C101", 198, 101, w=20),
            _item("大学英语", 100 + 6 * 32 + 3 * 32, 101, w=36),
        ]
    )

    blocks = schedule_core._parse_chinese_table_page_items(items, "张三", 220)

    occupied = {(block["weekday"], block["period"], block["course"]) for block in blocks}
    assert ("周一", 1, "高等数学") in occupied
    assert ("周一", 2, "高等数学") in occupied
    assert ("周二", 5, "大学英语") in occupied
    assert ("周二", 6, "大学英语") in occupied
    assert all(block["course"] not in {"无", "C101"} for block in blocks)


def test_english_date_grid_parser_maps_courses_by_period_columns():
    items = [_item(str(period), 90 + period * 28, 48, w=8) for period in range(1, 12)]
    items.extend(
        [
            _item("Monday", 20, 92, w=36),
            _item("2026-03-16", 20, 104, w=58),
            _item("Academic", 90 + 3 * 28, 96, w=34),
            _item("Writing", 90 + 3 * 28, 106, w=30),
            _item("Room", 90 + 4 * 28, 96, w=18),
        ]
    )

    blocks = schedule_core._parse_english_grid_page_items_from_pdf_words(items, "李四")

    assert len(blocks) == 1
    assert blocks[0]["name"] == "李四"
    assert blocks[0]["source"] == "英方"
    assert blocks[0]["week"] == 3
    assert blocks[0]["weekday"] == "周一"
    assert blocks[0]["period"] == 3
    assert blocks[0]["course"] == "Academic Writing"


def test_slot_table_reports_free_and_occupied_members():
    blocks = [schedule_core._block("张三", "中方", 3, schedule_core._date_for_weekday(3, "周一"), "周一", 1, "高等数学")]
    occupancy = schedule_core.build_occupancy(blocks)
    table = schedule_core.build_slot_table(occupancy, ["张三", "李四"], [3], ["周一"], [1])
    row = table.iloc[0]

    assert row["free_members"] == "李四"
    assert row["occupied_members"] == "张三"
    assert row["free_count"] == 1
    assert row["occupied_count"] == 1

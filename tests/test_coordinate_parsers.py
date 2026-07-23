from core import schedule_core


def test_default_timetable_uses_simple_cdut_schedule():
    timetable = schedule_core.default_timetable().set_index("period")

    assert f"{timetable.loc[1, 'start']}-{timetable.loc[1, 'end']}" == "08:10-08:55"
    assert f"{timetable.loc[12, 'start']}-{timetable.loc[12, 'end']}" == "12:40-13:25"
    assert f"{timetable.loc[13, 'start']}-{timetable.loc[13, 'end']}" == "13:30-14:15"
    assert f"{timetable.loc[5, 'start']}-{timetable.loc[5, 'end']}" == "14:30-15:15"
    assert f"{timetable.loc[6, 'start']}-{timetable.loc[6, 'end']}" == "15:20-16:05"
    assert f"{timetable.loc[11, 'start']}-{timetable.loc[11, 'end']}" == "20:50-21:35"


def test_detects_specialized_chinese_full_term_profile():
    items = [
        _item("成都理工大学本科学生课表（2025-2026学年第二学期）", 100, 20, w=220),
        _item("周/节", 20, 90, w=30),
        _item("星期一", 100, 90, w=30),
        _item("星期二", 200, 90, w=30),
        _item("1-23-4午5-67-8", 100, 110, w=80),
        _item("1周", 20, 140, w=20),
    ]

    profile = schedule_core._detect_schedule_layout_profile_from_text_items("中方", "", items)

    assert profile == "cdut_undergrad_full_term_cn"


def test_detects_specialized_chinese_full_term_profile_with_noisy_axis_tokens():
    items = [
        _item("成都理工大学本科学生课表（2025-2026学年第二学期）", 100, 20, w=220),
        _item("图节", 20, 90, w=30),
        _item("星期二", 200, 90, w=30),
        _item("1-234年5-67-8", 100, 110, w=80),
        _item("03/09-03/15", 20, 140, w=60),
    ]

    profile = schedule_core._detect_schedule_layout_profile_from_text_items("中方", "", items)

    assert profile == "cdut_undergrad_full_term_cn"


def test_detects_specialized_chinese_full_term_profile_with_compatibility_ideographs():
    text = "\n".join(
        [
            "成都理⼯⼤学本科学⽣课表(2025-2026学年第⼆学期)",
            "学号:202522020210 姓名李傲苒 ⽣成⽇期:2026-03-14",
            "周/节",
            "星期⼀",
            "星期⼆",
            "1-2",
            "3-4",
            "1周",
            "03/02-03/08",
        ]
    )

    profile = schedule_core._detect_schedule_layout_profile_from_text_items("中方", text, [])

    assert profile == "cdut_undergrad_full_term_cn"


def test_chinese_full_term_week_rows_accept_noisy_week_suffix():
    items = [
        _item("6同", 55, 220, w=20),
        _item("06/08-06/14", 20, 235, w=60),
        _item("8周", 55, 260, w=20),
        _item("06/22-06/28", 20, 275, w=60),
    ]

    rows = schedule_core._find_chinese_full_term_week_rows(items)

    assert [row["week"] for row in rows] == [6, 8]
    assert schedule_core._is_chinese_full_term_axis_token("6同")


def test_detects_specialized_english_web_profile():
    items = [
        _item("CDUTSino-BritishCollaborativeEducation-StudentWebsite", 60, 80, w=320),
        _item("TimetableforXingShihao", 60, 200, w=180),
        _item("Monday", 60, 350, w=40),
        _item("2026-05-04", 60, 365, w=60),
    ]

    profile = schedule_core._detect_schedule_layout_profile_from_text_items("英方", "", items)

    assert profile == "cdut_sino_british_english_web"


def test_noon_english_periods_do_not_occupy_afternoon_slots():
    weekday = schedule_core.WEEKDAYS[0]
    student = "student"
    occupancy = {(3, weekday, 12): {student}, (3, weekday, 13): {student}}

    assert schedule_core.period_time_range(12) == "12:40-13:25"
    table = schedule_core.build_slot_table(occupancy, [student], [3], [weekday], [5, 6])

    assert table["time"].tolist() == ["14:30-15:15", "15:20-16:05"]
    assert table["occupied_count"].tolist() == [0, 0]


def test_synthesized_calendar_covers_full_teaching_term():
    calendar = schedule_core.synthesize_calendar_from_blocks([])

    assert len(calendar) == 18 * 7
    assert calendar.iloc[0].to_dict() == {"date": "2026-03-02", "week": 1, "weekday": "周一"}
    assert calendar[(calendar["week"] == 6) & (calendar["weekday"] == "周一")].iloc[0]["date"] == "2026-04-06"
    assert calendar.iloc[-1].to_dict() == {"date": "2026-07-05", "week": 18, "weekday": "周日"}


def test_synthesized_calendar_uses_persisted_semester_environment(monkeypatch):
    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-09-07")
    monkeypatch.setenv("KONGGU_TEACHING_WEEKS", "20")

    calendar = schedule_core.synthesize_calendar_from_blocks([])

    assert len(calendar) == 20 * 7
    assert calendar.iloc[0].to_dict() == {"date": "2026-09-07", "week": 1, "weekday": "周一"}
    assert calendar[(calendar["week"] == 6) & (calendar["weekday"] == "周一")].iloc[0]["date"] == "2026-10-12"


def _item(text, x, y, w=18, h=8):
    return {"text": text, "x0": float(x), "y0": float(y), "x1": float(x + w), "y1": float(y + h)}


def _synthetic_full_term_grid_image():
    cv2 = __import__("cv2")
    np = __import__("numpy")
    image = np.full((520, 1160), 255, dtype=np.uint8)
    row_lines = [30, 48, 66] + [84 + index * 16 for index in range(21)]
    for y in row_lines:
        cv2.line(image, (20, y), (1120, y), 0, 1)

    day_left = 70
    sub_width = 12
    col_lines = [20, day_left]
    for index in range(7 * 12 + 1):
        col_lines.append(day_left + index * sub_width)
    for x in col_lines:
        cv2.line(image, (x, 30), (x, 420), 0, 1)

    week = 2
    top = row_lines[2 + week - 1]
    bottom = row_lines[2 + week]
    left = day_left
    right = day_left + sub_width * 2
    return image, {"left": left, "right": right, "top": top, "bottom": bottom}


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


def test_chinese_full_term_course_text_uses_legend_aliases():
    aliases = {"高", "中", "P"}

    assert schedule_core._is_chinese_full_term_course_text("高L31 E2B101", aliases)
    assert schedule_core._is_chinese_full_term_course_text("P L42 E2A104", aliases)
    assert not schedule_core._is_chinese_full_term_course_text("E2B101", aliases)
    assert not schedule_core._is_chinese_full_term_course_text("教妍", aliases)
    assert not schedule_core._is_chinese_full_term_course_text("请明节", aliases)


def test_chinese_full_term_visual_image_detects_occupied_cell():
    image, cell = _synthetic_full_term_grid_image()
    cv2 = __import__("cv2")
    cv2.rectangle(image, (cell["left"] + 4, cell["top"] + 4), (cell["right"] - 4, cell["bottom"] - 4), 0, -1)

    blocks = schedule_core._parse_chinese_full_term_visual_image(image, "张三", [])

    occupied = {(block["week"], block["weekday"], block["period"]) for block in blocks}
    assert (2, "周一", 1) in occupied
    assert (2, "周一", 2) in occupied


def test_chinese_full_term_visual_image_ignores_non_class_ocr_cell():
    image, cell = _synthetic_full_term_grid_image()
    cv2 = __import__("cv2")
    cv2.rectangle(image, (cell["left"] + 4, cell["top"] + 4), (cell["right"] - 4, cell["bottom"] - 4), 0, -1)
    ocr_items = [
        {
            "text": "清明节",
            "x0": cell["left"],
            "y0": cell["top"],
            "x1": cell["right"],
            "y1": cell["bottom"],
            "page_width": image.shape[1],
            "page_height": image.shape[0],
        }
    ]

    blocks = schedule_core._parse_chinese_full_term_visual_image(image, "张三", ocr_items)

    assert blocks == []


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


def test_english_date_grid_parser_accepts_ocr_date_tokens():
    items = [_item(str(period), 90 + period * 28, 48, w=8) for period in range(1, 12)]
    items.extend(
        [
            _item("Wednesday", 20, 92, w=54),
            _item("20260506", 20, 104, w=58),
            _item("ESAP-A", 90 + 1 * 28, 96, w=34),
            _item("Friday", 20, 145, w=36),
            _item("202605-08", 20, 157, w=58),
            _item("AE", 90 + 4 * 28, 149, w=18),
        ]
    )

    blocks = schedule_core._parse_english_grid_page_items_from_pdf_words(items, "罗思棋")

    occupied = {(block["date"], block["week"], block["weekday"], block["period"], block["course"]) for block in blocks}
    assert ("2026-05-06", 10, "周三", 1, "ESAP-A") in occupied
    assert ("2026-05-08", 10, "周五", 4, "AE") in occupied


def test_english_date_grid_parser_accepts_combined_weekday_date_token():
    items = [_item(str(period), 90 + period * 28, 48, w=8) for period in range(1, 12)]
    items.extend(
        [
            _item("Wednesday 20260506", 20, 100, w=82),
            _item("ESAP-A", 90 + 1 * 28, 96, w=34),
            _item("202605-08 Friday", 20, 145, w=82),
            _item("AE", 90 + 4 * 28, 149, w=18),
        ]
    )

    blocks = schedule_core._parse_english_grid_page_items_from_pdf_words(items, "罗思棋")

    occupied = {(block["date"], block["week"], block["weekday"], block["period"], block["course"]) for block in blocks}
    assert ("2026-05-06", 10, "周三", 1, "ESAP-A") in occupied
    assert ("2026-05-08", 10, "周五", 4, "AE") in occupied


def test_english_period_header_parser_accepts_noisy_ocr_tokens():
    items = [
        _item("A", 130, 48, w=24),
        _item("2", 180, 48, w=24),
        _item("3...4", 250, 48, w=80),
        _item(".5..6", 420, 48, w=80),
        _item(".7.8", 620, 48, w=80),
        _item("9", 790, 48, w=24),
        _item("10", 840, 48, w=28),
        _item(".11", 890, 48, w=30),
        _item("Wednesday", 20, 92, w=54),
        _item("2026-05-06", 20, 104, w=58),
        _item("AE", 255, 96, w=20),
    ]

    blocks = schedule_core._parse_english_grid_page_items_from_pdf_words(items, "邢时豪")

    assert any(block["date"] == "2026-05-06" and block["period"] == 3 and block["course"] == "AE" for block in blocks)


def test_english_period_header_parser_completes_partial_ocr_header():
    items = [
        _item("A", 130, 48, w=24),
        _item("2", 180, 48, w=24),
        _item("3", 250, 48, w=24),
        _item(".4", 305, 48, w=24),
        _item("8", 675, 48, w=24),
        _item("10", 840, 48, w=28),
        _item(".11", 890, 48, w=30),
        _item("Wednesday", 20, 92, w=54),
        _item("2026-05-06", 20, 104, w=58),
        _item("GE", 535, 96, w=20),
    ]

    blocks = schedule_core._parse_english_grid_page_items_from_pdf_words(items, "邢时豪")

    assert any(block["date"] == "2026-05-06" and block["period"] in {5, 6} and block["course"] == "GE" for block in blocks)


def test_english_period_header_parser_uses_noisy_time_tokens():
    items = [
        _item("98:105-", 130, 60, w=36),
        _item("9%:045-", 180, 60, w=36),
        _item("10:66-", 260, 60, w=36),
        _item("11:956-", 310, 60, w=36),
        _item("153", 520, 60, w=28),
        _item("15205-", 565, 60, w=36),
        _item("16:26", 640, 60, w=36),
        _item("178:156-", 690, 60, w=36),
        _item("1%15", 815, 60, w=34),
        _item("206:45", 860, 60, w=36),
        _item("20155", 910, 60, w=36),
        _item("L4B-4", 525, 110, w=34),
        _item("Wednesday", 20, 105, w=54),
        _item("2026-05-06", 20, 117, w=58),
        _item("EIBM", 535, 109, w=30),
        _item("TE", 690, 109, w=20),
    ]

    blocks = schedule_core._parse_english_grid_page_items_from_pdf_words(items, "唐洋")

    occupied = {(block["date"], block["period"], block["course"]) for block in blocks}
    assert ("2026-05-06", 5, "EIBM") in occupied
    assert ("2026-05-06", 8, "TE") in occupied
    assert schedule_core._extract_english_period_header_numbers("L4B-4") == []


def test_finalize_filters_non_courses_and_recalculates_week_from_date():
    blocks = [
        schedule_core._block("李四", "英方", 1, schedule_core._parse_date("2026-05-11"), "周一", 1, "ESAP-B"),
        schedule_core._block("李四", "英方", 1, schedule_core._parse_date("2026-05-11"), "周一", 2, "- AE"),
        schedule_core._block("李四", "英方", 1, schedule_core._parse_date("2026-05-12"), "周二", 3, "https://example.com"),
        schedule_core._block("李四", "中方", 2, schedule_core._parse_date("2026-03-11"), "周三", 7, "教研"),
        schedule_core._block("李四", "中方", 2, schedule_core._parse_date("2026-03-12"), "周四", 8, "校外"),
        schedule_core._block("李四", "中方", 19, schedule_core._parse_date("2026-07-06"), "周一", 1, "期末周"),
        schedule_core._block("李四", "中方", 20, schedule_core._parse_date("2026-07-13"), "周一", 1, "考试周"),
    ]

    finalized = schedule_core._finalize_parsed_blocks(blocks, "source.pdf", "英方")

    assert len(finalized) == 1
    assert finalized[0]["course"] == "ESAP-B"
    assert finalized[0]["week"] == 11
    assert finalized[0]["date"] == "2026-05-11"
    assert finalized[0]["weekday"] == "周一"


def test_low_confidence_chinese_ocr_result_flags_fragment_heavy_blocks():
    blocks = []
    for index in range(30):
        course = "2075-04-25" if index < 20 else "高等数学"
        blocks.append(
            schedule_core._block(
                "罗思棋",
                "中方",
                2,
                schedule_core._parse_date("2026-03-10"),
                "周二",
                (index % 11) + 1,
                course,
            )
        )

    assert schedule_core._is_low_confidence_chinese_ocr_result(blocks, used_ocr=True)
    assert not schedule_core._is_low_confidence_chinese_ocr_result(blocks, used_ocr=False)
    metrics = schedule_core._course_fragment_metrics(blocks, source="中方")
    assert metrics["count"] == 30
    assert metrics["fragment_count"] == 20
    assert metrics["fragment_ratio"] > 0.6


def test_low_confidence_chinese_ocr_result_handles_smaller_fragment_heavy_parse():
    blocks = []
    for index in range(12):
        course = "AL3" if index < 7 else "高等数学"
        blocks.append(
            schedule_core._block(
                "罗思棋",
                "中方",
                2,
                schedule_core._parse_date("2026-03-10"),
                "周二",
                (index % 11) + 1,
                course,
            )
        )

    assert schedule_core._is_low_confidence_chinese_ocr_result(blocks, used_ocr=True)


def test_low_confidence_chinese_ocr_result_accepts_encoded_course_cells():
    courses = ["大R414 TT06", "高L29 E2B202", "中L2 E2C404", "形L67 E1A504"]
    blocks = [
        schedule_core._block(
            "高彩伦",
            "中方",
            3,
            schedule_core._parse_date("2026-03-16"),
            "周一",
            (index % 11) + 1,
            courses[index % len(courses)],
        )
        for index in range(32)
    ]

    assert not schedule_core._is_low_confidence_chinese_ocr_result(blocks, used_ocr=True)
    metrics = schedule_core._course_fragment_metrics(blocks, source="中方")
    assert metrics["fragment_count"] == 0


def test_chinese_full_term_ocr_confidence_flags_sparse_week_coverage():
    weeks = [2, 4, 6, 9, 11, 15, 17, 18]
    blocks = [
        schedule_core._block(
            "罗思棋",
            "中方",
            weeks[index % len(weeks)],
            schedule_core._date_for_weekday(weeks[index % len(weeks)], schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "高L31 E2B101",
        )
        for index in range(59)
    ]

    issue = schedule_core._chinese_full_term_ocr_confidence_issue(blocks, used_ocr=True)

    assert "只覆盖 8 个离散周次" in issue
    assert not schedule_core._chinese_full_term_ocr_confidence_issue(blocks, used_ocr=False)


def test_chinese_full_term_ocr_confidence_accepts_broad_week_coverage():
    weeks = list(range(1, 19))
    blocks = [
        schedule_core._block(
            "高彩伦",
            "中方",
            weeks[index % len(weeks)],
            schedule_core._date_for_weekday(weeks[index % len(weeks)], schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "高L31 E2B101",
        )
        for index in range(72)
    ]

    assert schedule_core._chinese_full_term_ocr_confidence_issue(blocks, used_ocr=True) == ""


def test_prefers_chinese_full_term_visual_when_it_is_substantially_more_complete():
    parsed = [
        schedule_core._block(
            "李梦琪",
            "中方",
            1 + index % 17,
            schedule_core._date_for_weekday(1 + index % 17, schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "社L5",
        )
        for index in range(72)
    ]
    visual = [
        schedule_core._block(
            "李梦琪",
            "中方",
            1 + index % 18,
            schedule_core._date_for_weekday(1 + index % 18, schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "视觉表格占用",
        )
        for index in range(160)
    ]

    assert schedule_core._should_prefer_chinese_full_term_visual_result(parsed, visual)


def test_keeps_chinese_full_term_ocr_when_visual_is_only_slightly_larger():
    parsed = [
        schedule_core._block(
            "高彩伦",
            "中方",
            1 + index % 18,
            schedule_core._date_for_weekday(1 + index % 18, schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "高L31",
        )
        for index in range(220)
    ]
    visual = [
        schedule_core._block(
            "高彩伦",
            "中方",
            1 + index % 18,
            schedule_core._date_for_weekday(1 + index % 18, schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "视觉表格占用",
        )
        for index in range(250)
    ]

    assert not schedule_core._should_prefer_chinese_full_term_visual_result(parsed, visual)


def test_chinese_ocr_candidate_picker_prefers_clean_table_parse():
    text_parsed = [
        schedule_core._block(
            "罗思棋",
            "中方",
            1 + index // 5,
            schedule_core._date_for_weekday(1 + index // 5, schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "2075-04-25" if index < 20 else "高等数学",
        )
        for index in range(30)
    ]
    table_parsed = [
        schedule_core._block(
            "罗思棋",
            "中方",
            1 + index // 5,
            schedule_core._date_for_weekday(1 + index // 5, schedule_core.WEEKDAYS[index % 5]),
            schedule_core.WEEKDAYS[index % 5],
            (index % 11) + 1,
            "高L31",
        )
        for index in range(12)
    ]

    chosen = schedule_core._choose_chinese_ocr_parse_candidate(text_parsed, table_parsed, "source.pdf")

    assert chosen == table_parsed


def test_extract_chinese_name_from_text_reads_ocr_header():
    text = "成都理工大学本科学生课表\n学号：202522040120\n姓名：王婧琪\n班级：2025220401"

    assert schedule_core._extract_chinese_name_from_text(text) == "王婧琪"


def test_low_confidence_english_ocr_result_flags_sparse_image_parse():
    blocks = [
        schedule_core._block(
            "唐洋",
            "英方",
            11,
            schedule_core._parse_date("2026-05-11"),
            "周一",
            period,
            "GE",
        )
        for period in range(1, 6)
    ]

    assert schedule_core._is_low_confidence_english_ocr_result(blocks, used_ocr=True)
    assert not schedule_core._is_low_confidence_english_ocr_result(blocks * 2, used_ocr=True)
    assert not schedule_core._is_low_confidence_english_ocr_result(blocks, used_ocr=False)


def test_slot_table_reports_free_and_occupied_members():
    blocks = [schedule_core._block("张三", "中方", 3, schedule_core._date_for_weekday(3, "周一"), "周一", 1, "高等数学")]
    occupancy = schedule_core.build_occupancy(blocks)
    table = schedule_core.build_slot_table(occupancy, ["张三", "李四"], [3], ["周一"], [1])
    row = table.iloc[0]

    assert row["free_members"] == "李四"
    assert row["occupied_members"] == "张三"
    assert row["free_count"] == 1
    assert row["occupied_count"] == 1

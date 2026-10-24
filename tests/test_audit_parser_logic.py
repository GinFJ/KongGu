"""Audit regressions for the parser core and the export boundary.

Each test pins one provable defect found while auditing the parse pipeline:
PDF source preflight -> parse_actual_pdf_sources -> layout/OCR fallback ->
coordinate clustering -> date/period mapping -> cache -> export boundary.
"""

from io import BytesIO
import json

import fitz
from openpyxl import load_workbook
import pandas as pd
import pytest

from core import schedule_core
from core.runtime_control import cancellation_scope
from core.signature import build_parser_signature, cache_signature


def _item(text, x, y, w=18, h=8):
    return {"text": text, "x0": float(x), "y0": float(y), "x1": float(x + w), "y1": float(y + h)}


def _typed_block(weekday="周一", period=1, course="高等数学", week=1):
    return schedule_core._block(
        "张三",
        "中方",
        week,
        schedule_core._date_for_weekday(week, weekday),
        weekday,
        period,
        course,
    )


def _blank_pdf(path, pages=1):
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    document.save(str(path))
    document.close()


def _chinese_table_items():
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
            _item("5周", 20, 130, w=20),
            _item("13/45-13/51", 48, 130, w=60),
            _item("大学英语", 102, 131, w=36),
        ]
    )
    return items


# ---------------------------------------------------------------------------
# Export boundary: non-scalar DataFrame cells must not break the workbook.
# ---------------------------------------------------------------------------


def test_export_flattens_list_cell_from_generated_blocks_dataframe():
    """Export must not fail on the ``periods`` list added to legacy blocks.

    The list cell used to break openpyxl export of every accepted result.  The
    export boundary now projects blocks onto the user-facing columns, so the
    internal list column is dropped instead of being flattened into the
    workbook; the export must still succeed and keep the normal columns.
    """

    blocks = [dict(_typed_block(period=1), periods=[1, 2])]
    blocks_df = schedule_core.blocks_to_dataframe(blocks)
    assert isinstance(blocks_df.iloc[0]["periods"], list)

    data = schedule_core.build_empty_schedule_excel_bytes(
        occupancy={(1, "周一", 1): {"张三"}},
        students=["张三"],
        weeks=[1],
        calendar_df=pd.DataFrame(),
        timetable_df=schedule_core.default_timetable(),
        blocks_df=blocks_df,
        all_slot_df=None,
    )

    workbook = load_workbook(BytesIO(data), read_only=True)
    try:
        rows = list(workbook["课程占用明细"].iter_rows(values_only=True))
    finally:
        workbook.close()
    header = list(rows[0])
    values = rows[1]
    assert "periods" not in header
    assert values[header.index("节次")] == 1
    assert values[header.index("成员")] == "张三"


def test_export_flattens_dict_and_tuple_cells_instead_of_raising():
    blocks_df = pd.DataFrame(
        [
            {
                "name": "张三",
                "week": 1,
                "weekday": "周一",
                "period": 1,
                "course": "课程",
                "periods": (1, 2),
                "bbox": (12.5, 30.0, 80.0, 44.0),
                "provenance": {"page": 1, "text_source": "内嵌文本"},
            }
        ]
    )

    data = schedule_core.build_empty_schedule_excel_bytes(
        occupancy={(1, "周一", 1): {"张三"}},
        students=["张三"],
        weeks=[1],
        calendar_df=pd.DataFrame(),
        timetable_df=schedule_core.default_timetable(),
        blocks_df=blocks_df,
        all_slot_df=None,
    )

    workbook = load_workbook(BytesIO(data), read_only=True)
    try:
        rows = list(workbook["成员课程明细"].iter_rows(values_only=True))
    finally:
        workbook.close()
    header = list(rows[0])
    values = rows[1]
    # 非标量内部列不再进入导出物，导出仍必须成功且保留用户列。
    for internal in ("periods", "bbox", "provenance"):
        assert internal not in header
    assert values[header.index("成员")] == "张三"
    assert values[header.index("课程")] == "课程"


# ---------------------------------------------------------------------------
# Date rows: one noisy token must not abort the whole PDF parse.
# ---------------------------------------------------------------------------


def test_parse_week_start_returns_none_for_invalid_month_day():
    assert schedule_core._parse_week_start("13/45-13/51", 5) is None
    assert schedule_core._parse_week_start("03/16", 3) == schedule_core._date_for_weekday(3, "周一")


def test_chinese_table_noisy_date_row_falls_back_to_synthetic_dates():
    blocks = schedule_core._parse_chinese_table_page_items(_chinese_table_items(), "张三", 220)

    by_week = {(block["week"], block["course"]) for block in blocks}
    assert (3, "高等数学") in by_week
    assert (5, "大学英语") in by_week
    week_five = [block for block in blocks if block["week"] == 5]
    assert {block["date"] for block in week_five} == {
        schedule_core._date_for_weekday(5, "周一").isoformat()
    }


# ---------------------------------------------------------------------------
# English period header: interior OCR gaps must be completed.
# ---------------------------------------------------------------------------


def test_english_header_completion_fills_interior_gaps_above_eight_tokens():
    header_periods = [1, 2, 3, 4, 5, 6, 7, 8, 11]
    x_for = {period: 90 + (period - 1) * 40 for period in range(1, 12)}
    items = [_item(str(period), x_for[period], 48, w=10) for period in header_periods]
    items.extend(
        [
            _item("Monday", 20, 92, w=36),
            _item("2026-03-16", 20, 104, w=58),
            _item("Physics", x_for[9], 96, w=34),
            _item("Math", x_for[10], 96, w=30),
        ]
    )

    blocks = schedule_core._parse_english_grid_page_items_from_pdf_words(items, "李四")

    assert {(block["period"], block["course"]) for block in blocks} == {(9, "Physics"), (10, "Math")}


def test_english_header_completion_marks_interpolated_columns_as_synthetic():
    by_period = {
        period: _item(str(period), 90 + (period - 1) * 40, 48, w=10)
        for period in [1, 2, 3, 4, 5, 6, 7, 8, 11]
    }

    columns = schedule_core._complete_english_period_columns(by_period)

    assert [column["period"] for column in columns] == list(range(1, 12))
    assert {column["period"] for column in columns if column["synthetic"]} == {9, 10}
    assert columns[8]["center"] == pytest.approx(415.0)
    assert columns[9]["center"] == pytest.approx(455.0)


# ---------------------------------------------------------------------------
# OCR: document handles and cache entries must not outlive a failed run.
# ---------------------------------------------------------------------------


class _FakePixmap:
    width = 4
    height = 4
    n = 3
    samples = bytes([255]) * (4 * 4 * 3)


class _FakePage:
    def get_pixmap(self, **kwargs):
        return _FakePixmap()


class _FakeDocument:
    def __init__(self, pages=1):
        self.page_count = pages
        self.closed = False

    def __iter__(self):
        return iter([_FakePage() for _ in range(self.page_count)])

    def close(self):
        self.closed = True


def _ocr_source(token: bytes) -> dict:
    return {
        "file_name": "成员甲-中方课表.pdf",
        "kind": "中方",
        "bytes": token,
        "source_path": "D:/fake/成员甲-中方课表.pdf",
    }


def test_ocr_items_closes_document_when_ocr_engine_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_OCR_LAYOUT_CACHE", str(tmp_path))
    document = _FakeDocument()
    monkeypatch.setattr(schedule_core, "_open_pdf_document", lambda _source: document)

    def broken_engine():
        raise schedule_core.OCRConfigurationError("OCR 配置异常：测试")

    monkeypatch.setattr(schedule_core, "_build_ocr_engine", broken_engine)

    with pytest.raises(schedule_core.OCRConfigurationError):
        schedule_core._extract_pdf_ocr_items(_ocr_source(b"raw-pdf-engine-unavailable"))

    assert document.closed is True


def test_ocr_items_ignores_empty_layout_cache_and_retries_ocr(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_OCR_LAYOUT_CACHE", str(tmp_path))
    source = _ocr_source(b"raw-pdf-empty-layout-cache")
    cache_path = schedule_core._ocr_layout_cache_path(source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "version": schedule_core.OCR_LAYOUT_CACHE_VERSION,
                "parser_signature": cache_signature(),
                "hash": schedule_core._source_digest(source),
                "file_name": schedule_core._source_name(source),
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    class FakeEngine:
        def __init__(self):
            self.calls = 0

        def ocr(self, image):
            self.calls += 1
            return None

    engine = FakeEngine()
    document = _FakeDocument()
    monkeypatch.setattr(schedule_core, "_open_pdf_document", lambda _source: document)
    monkeypatch.setattr(schedule_core, "_build_ocr_engine", lambda: engine)

    assert schedule_core._extract_pdf_ocr_items(source) == []
    assert engine.calls == 1
    assert document.closed is True


def test_ocr_items_does_not_persist_empty_layout_result(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_OCR_LAYOUT_CACHE", str(tmp_path))
    source = _ocr_source(b"raw-pdf-empty-layout-result")

    class FakeEngine:
        def ocr(self, image):
            return None

    monkeypatch.setattr(schedule_core, "_open_pdf_document", lambda _source: _FakeDocument())
    monkeypatch.setattr(schedule_core, "_build_ocr_engine", lambda: FakeEngine())

    assert schedule_core._extract_pdf_ocr_items(source) == []
    assert not schedule_core._ocr_layout_cache_path(source).exists()


def test_chinese_legacy_web_layout_closes_document(monkeypatch, tmp_path):
    pdf = tmp_path / "legacy.pdf"
    pdf.write_bytes(b"placeholder")

    class FakeLegacyDocument:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            return iter([])

        def close(self):
            self.closed = True

    document = FakeLegacyDocument()
    monkeypatch.setattr(fitz, "open", lambda *args, **kwargs: document)
    monkeypatch.setattr(schedule_core, "_extract_pdf_text", lambda _source: "旧版课表打印 学期理论课表")

    blocks = schedule_core._parse_chinese_legacy_web_layout(
        {"source_path": str(pdf), "file_name": pdf.name}, pdf.name
    )

    assert blocks == []
    assert document.closed is True


def test_pdf_source_keeps_hash_not_bytes_and_verifies_before_parsing(tmp_path, monkeypatch):
    """A 200 MB batch must not stay resident: files are re-read and verified one
    at a time, and a file that changed after import is refused instead of parsed
    with a mismatching source hash."""

    from core.legacy_adapter import model_source_to_legacy, pdf_source_from_path

    def pdf_bytes(text):
        document = fitz.open()
        document.new_page().insert_text((72, 72), text)
        data = document.tobytes()
        document.close()
        return data

    path = tmp_path / "成员甲-中方课表.pdf"
    path.write_bytes(pdf_bytes("Captured source A"))
    model = pdf_source_from_path(path, "中方")
    source = model_source_to_legacy(model)

    # The model keeps the hash but not the bytes.
    assert model.bytes_data is None
    assert model.content_hash == schedule_core._file_digest(str(path))
    assert source["bytes"] is None
    assert schedule_core._source_digest(source) == source["content_hash"]

    # Parsing one file materializes and releases its bytes.
    assert schedule_core._materialize_source_content(source) == ""
    assert source["content"] == path.read_bytes()
    source.pop("content", None)

    def boom(*args, **kwargs):
        raise RuntimeError("解析入口已桩化")

    monkeypatch.setattr(schedule_core, "_parse_chinese_pdf_layout", boom)
    blocks, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources([source])
    assert blocks == []
    assert any("解析入口已桩化" in error for error in errors)
    assert "content" not in source

    # The same import record must refuse a file that changed on disk afterwards.
    path.write_bytes(pdf_bytes("Changed source B"))
    changed_blocks, _calendar, changed_errors, _preview = schedule_core.parse_actual_pdf_sources([source])
    assert changed_blocks == []
    assert any("导入后发生变化" in error for error in changed_errors)
    assert "content" not in source


def test_page_image_cache_reuses_identical_render(monkeypatch):
    counters = {"renders": 0}

    class FakePage:
        def get_pixmap(self, **kwargs):
            counters["renders"] += 1
            return _FakePixmap()

    source = {"file_name": "成员甲-中方课表.pdf", "kind": "中方", "bytes": b"page-cache-source"}
    page = FakePage()
    schedule_core.clear_page_image_cache()

    first = schedule_core._page_image(source, page, 0, 120)
    second = schedule_core._page_image(source, page, 0, 120)
    other_dpi = schedule_core._page_image(source, page, 0, 180)

    assert counters["renders"] == 2
    assert first is second
    assert other_dpi is not first

    schedule_core.clear_page_image_cache()
    schedule_core._page_image(source, page, 0, 120)
    assert counters["renders"] == 3


def test_clip_check_cache_skips_second_render(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_RASTER_CLIP_CACHE", str(tmp_path / "clip"))
    monkeypatch.setenv("KONGGU_OCR_LAYOUT_CACHE", str(tmp_path / "layout"))
    header = {
        "page": 0,
        "text": "星期一",
        "x0": 100,
        "x1": 140,
        "y0": 10,
        "y1": 20,
        "page_width": 1000,
        "page_height": 1400,
    }
    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_items", lambda source, **kwargs: [dict(header)])
    counters = {"opens": 0, "renders": 0}

    class FakePage:
        def get_pixmap(self, **kwargs):
            counters["renders"] += 1
            return _FakePixmap()

    class FakeDoc:
        page_count = 1

        def __iter__(self):
            return iter([FakePage()])

        def close(self):
            pass

    def fake_open(source):
        counters["opens"] += 1
        return FakeDoc()

    monkeypatch.setattr(schedule_core, "_open_pdf_document", fake_open)
    schedule_core.clear_page_image_cache()
    source = {"file_name": "成员甲-中方课表.pdf", "kind": "中方", "bytes": b"clip-cache-source"}

    first = schedule_core.chinese_image_clipped_pages(source)
    second = schedule_core.chinese_image_clipped_pages(source)

    assert first == second == []
    assert counters["opens"] == 1
    assert counters["renders"] == 1


def test_semester_start_date_and_weeks_follow_environment(monkeypatch):
    from datetime import date

    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-09-07")
    monkeypatch.setenv("KONGGU_TEACHING_WEEKS", "20")
    assert schedule_core._semester_start_date() == date(2026, 9, 7)
    assert schedule_core._teaching_weeks() == 20

    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2027-03-01")
    monkeypatch.setenv("KONGGU_TEACHING_WEEKS", "18")
    assert schedule_core._semester_start_date() == date(2027, 3, 1)
    assert schedule_core._teaching_weeks() == 18

    monkeypatch.delenv("KONGGU_SEMESTER_START_DATE")
    monkeypatch.delenv("KONGGU_TEACHING_WEEKS")
    assert schedule_core._semester_start_date() == date(2026, 3, 2)
    assert schedule_core._teaching_weeks() == 18


# ---------------------------------------------------------------------------
# Cancellation: page loops of long PDF parses must honour the job cancel flag.
# ---------------------------------------------------------------------------


def test_chinese_pdf_layout_page_loop_respects_cancellation(tmp_path):
    pdf = tmp_path / "blank-cn.pdf"
    _blank_pdf(pdf, pages=2)
    source = {"source_path": str(pdf), "file_name": "成员甲-中方课表.pdf"}

    with cancellation_scope(lambda: True):
        with pytest.raises(InterruptedError):
            schedule_core._parse_chinese_pdf_layout(source, "成员甲-中方课表.pdf")


def test_full_term_visual_page_loop_respects_cancellation(tmp_path, monkeypatch):
    pdf = tmp_path / "blank-visual.pdf"
    _blank_pdf(pdf, pages=2)
    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_items", lambda *args, **kwargs: [])
    source = {"source_path": str(pdf), "file_name": "成员甲-中方课表.pdf"}

    with cancellation_scope(lambda: True):
        with pytest.raises(InterruptedError):
            schedule_core._parse_chinese_full_term_visual_occupancy(source, "成员甲-中方课表.pdf")


# ---------------------------------------------------------------------------
# Deterministic ordering of the preview/export block table.
# ---------------------------------------------------------------------------


def test_blocks_to_dataframe_sorts_in_teaching_order():
    blocks = [
        _typed_block("周日", 1, "周日课程"),
        _typed_block("周一", 5, "周一第五"),
        _typed_block("周一", 12, "周一中午"),
        _typed_block("周三", 13, "周三中午"),
    ]

    frame = schedule_core.blocks_to_dataframe(blocks)

    assert frame["weekday"].tolist() == ["周一", "周一", "周三", "周日"]
    assert frame["period"].tolist() == [12, 5, 13, 1]


def test_chinese_text_fallback_ignores_bottom_legend_entries(monkeypatch):
    """Trailing course legend lines must not become course occupancy.

    Text-layer full-term timetables end with a course legend (course number,
    week/period range, teacher).  The line-based fallback used to keep consuming
    those lines as cells and produced phantom blocks after the last week row
    (18+ blocks covering every period of one weekday).
    """

    lines = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    for week, date_text in ((1, "03/02"), (2, "03/09"), (3, "03/16")):
        lines.extend([f"{week}周", date_text, "高等数学", "无", "无", "无", "无", "无"])
    lines.extend(
        [
            "高等数学(高)[1-16周][1-2节] 2024-2025-1 教师:某",
            "大学英语(大)[1-8周][3-4节] 2024-2025-1 教师:某",
        ]
    )

    blocks = schedule_core._parse_chinese_text("\n".join(lines), "成员甲-中方课表.pdf")

    assert len(blocks) == 6
    assert all("[" not in block["course"] for block in blocks)
    assert {block["week"] for block in blocks} == {1, 2, 3}
    assert {(block["weekday"], block["period"]) for block in blocks} == {("周一", 1), ("周一", 2)}


# ---------------------------------------------------------------------------
# Parser signature: effective semester calendar must invalidate caches.
# ---------------------------------------------------------------------------


def test_parser_signature_tracks_effective_semester_calendar(monkeypatch):
    base = build_parser_signature()
    assert base.calendar_start == schedule_core._semester_start_date().isoformat()
    assert base.teaching_weeks == str(schedule_core._teaching_weeks())
    assert base.as_dict()["calendar_start"] == base.calendar_start

    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-09-07")
    moved = build_parser_signature()
    assert moved.digest != base.digest
    assert moved.calendar_start == "2026-09-07"

    monkeypatch.setenv("KONGGU_TEACHING_WEEKS", "20")
    longer = build_parser_signature()
    assert longer.digest != moved.digest
    assert longer.teaching_weeks == "20"
    assert build_parser_signature().digest == longer.digest


def test_parse_cache_is_invalidated_when_semester_settings_change(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_PARSE_CACHE", str(tmp_path))
    source = _ocr_source(b"raw-pdf-semester-cache")
    blocks = [dict(_typed_block(period=1), periods=[1, 2])]

    schedule_core._write_parse_cache(source, "中方", blocks)
    assert schedule_core._load_parse_cache(source, "中方") == blocks

    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-09-07")

    assert schedule_core._load_parse_cache(source, "中方") is None

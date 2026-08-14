from core import schedule_core
from core.errors import ErrorType
from core.legacy_adapter import build_file_records, course_blocks_from_legacy
from core.models import PdfSource


def test_image_only_pdf_uses_ocr_text(monkeypatch):
    source = {"file_name": "张三-中方课表.pdf", "kind": "中方", "source_path": "D:/fake.pdf"}

    monkeypatch.setattr(schedule_core, "_load_parse_cache", lambda source, kind: None)
    monkeypatch.setattr(schedule_core, "_write_parse_cache", lambda source, kind, blocks: None)
    monkeypatch.setattr(schedule_core, "_parse_chinese_pdf_layout", lambda source, file_name: [])
    monkeypatch.setattr(schedule_core, "_parse_chinese_legacy_web_layout", lambda source, file_name: [])
    monkeypatch.setattr(schedule_core, "_extract_pdf_text", lambda source: "")
    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_text", lambda source: "OCR text with schedule")
    monkeypatch.setattr(
        schedule_core,
        "_parse_chinese_text",
        lambda text, file_name: [
            {
                "name": "张三",
                "source": "中方",
                "source_type": "中方",
                "kind": "中方",
                "week": 3,
                "date": "2026-03-16",
                "weekday": "周一",
                "period": 1,
                "periods": [1],
                "time": "08:10-08:55",
                "course": "高等数学",
                "source_file": file_name,
            }
        ],
    )

    blocks, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources([source])

    assert errors == []
    assert len(blocks) == 1
    assert blocks[0]["text_source"] == "OCR"


def test_parse_actual_pdf_sources_reports_each_completed_file(monkeypatch):
    sources = [
        {"file_name": "张三-中方课表.pdf", "kind": "中方", "source_path": "D:/fake-a.pdf"},
        {"file_name": "李四-中方课表.pdf", "kind": "中方", "source_path": "D:/fake-b.pdf"},
    ]
    events = []
    block = {
        "name": "成员",
        "source": "中方",
        "source_type": "中方",
        "kind": "中方",
        "week": 3,
        "date": "2026-03-16",
        "weekday": "周一",
        "period": 1,
        "periods": [1],
        "time": "08:10-08:55",
        "course": "高等数学",
    }

    monkeypatch.setattr(schedule_core, "_load_parse_cache", lambda source, kind: None)
    monkeypatch.setattr(schedule_core, "_parse_chinese_pdf_layout", lambda source, file_name: [dict(block)])
    monkeypatch.setattr(schedule_core, "_finalize_parsed_blocks", lambda parsed, file_name, kind: parsed)
    monkeypatch.setattr(schedule_core, "_write_parse_cache", lambda source, kind, blocks: None)

    parsed, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources(sources, progress=lambda *event: events.append(event))

    assert len(parsed) == 2
    assert errors == []
    assert [(event[0], event[1]) for event in events] == [(1, 2), (2, 2)]


def test_coordinate_layout_results_keep_embedded_text_source(monkeypatch):
    source = {"file_name": "张三-中方课表.pdf", "kind": "中方", "source_path": "D:/fake.pdf"}
    block = {
        "name": "张三",
        "source": "中方",
        "source_type": "中方",
        "kind": "中方",
        "week": 3,
        "date": "2026-03-16",
        "weekday": "周一",
        "period": 1,
        "periods": [1],
        "time": "08:10-08:55",
        "course": "高等数学",
    }

    monkeypatch.setattr(schedule_core, "_load_parse_cache", lambda source, kind: None)
    monkeypatch.setattr(schedule_core, "_parse_chinese_pdf_layout", lambda source, file_name: [dict(block)])
    monkeypatch.setattr(schedule_core, "_finalize_parsed_blocks", lambda parsed, file_name, kind: parsed)
    monkeypatch.setattr(schedule_core, "_write_parse_cache", lambda source, kind, blocks: None)

    parsed, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources([source])

    assert errors == []
    assert parsed[0]["text_source"] == "内嵌文本"


def test_english_coordinate_results_with_full_coverage_skip_ocr(monkeypatch):
    source = {"file_name": "李四-英方课表.pdf", "kind": "英方", "source_path": "D:/fake.pdf"}
    block = {
        "name": "李四",
        "source": "英方",
        "source_type": "英方",
        "kind": "英方",
        "week": 3,
        "date": "2026-03-16",
        "weekday": "周一",
        "period": 1,
        "periods": [1],
        "time": "08:10-08:55",
        "course": "Computer Science",
    }

    monkeypatch.setattr(schedule_core, "_load_parse_cache", lambda source, kind: None)
    monkeypatch.setattr(schedule_core, "_parse_english_pdf_grid_layout", lambda source, file_name: [dict(block) for _ in range(8)])
    monkeypatch.setattr(schedule_core, "_finalize_parsed_blocks", lambda parsed, file_name, kind: parsed)
    monkeypatch.setattr(schedule_core, "_write_parse_cache", lambda source, kind, blocks: None)

    parsed, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources([source])

    assert errors == []
    assert len(parsed) == 8
    assert {item["text_source"] for item in parsed} == {"内嵌文本"}


def test_ocr_fallback_reports_page_progress(monkeypatch):
    source = {"file_name": "张三-中方课表.pdf", "kind": "中方", "source_path": "D:/fake.pdf"}
    events = []
    block = {
        "name": "张三",
        "source": "中方",
        "source_type": "中方",
        "kind": "中方",
        "week": 3,
        "date": "2026-03-16",
        "weekday": "周一",
        "period": 1,
        "periods": [1],
        "time": "08:10-08:55",
        "course": "高等数学",
    }

    monkeypatch.setattr(schedule_core, "_load_parse_cache", lambda source, kind: None)
    monkeypatch.setattr(schedule_core, "_parse_chinese_pdf_layout", lambda source, file_name: [])
    monkeypatch.setattr(schedule_core, "_parse_chinese_legacy_web_layout", lambda source, file_name: [])
    monkeypatch.setattr(schedule_core, "_extract_pdf_text", lambda source: "")

    def fake_ocr_text(source, *, progress=None):
        if progress:
            progress(1, 2)
        return "OCR text with schedule"

    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_text", fake_ocr_text)
    monkeypatch.setattr(schedule_core, "_parse_chinese_text", lambda text, file_name: [dict(block)])
    monkeypatch.setattr(schedule_core, "_write_parse_cache", lambda source, kind, blocks: None)

    parsed, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources(
        [source],
        progress=lambda *event: events.append(event),
    )

    assert errors == []
    assert len(parsed) == 1
    assert any(event[0:2] == (0, 1) and "第 1/2 页" in event[2] for event in events)
    assert events[-1][0:2] == (1, 1)


def test_image_only_pdf_reports_ocr_configuration_error(monkeypatch):
    source = {"file_name": "张三-中方课表.pdf", "kind": "中方", "source_path": "D:/fake.pdf"}

    monkeypatch.setattr(schedule_core, "_load_parse_cache", lambda source, kind: None)
    monkeypatch.setattr(schedule_core, "_parse_chinese_pdf_layout", lambda source, file_name: [])
    monkeypatch.setattr(schedule_core, "_parse_chinese_legacy_web_layout", lambda source, file_name: [])
    monkeypatch.setattr(schedule_core, "_extract_pdf_text", lambda source: "")

    def raise_ocr_error(source):
        raise schedule_core.OCRConfigurationError("OCR 配置异常：pipeline (OCR) does not exist")

    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_text", raise_ocr_error)

    blocks, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources([source])

    assert blocks == []
    assert len(errors) == 1
    assert "图片型 PDF 需要 OCR" in errors[0]
    assert "OCR 配置异常" in errors[0]


def test_file_records_classify_ocr_configuration_errors():
    source = PdfSource("张三-中方课表.pdf", "中方", "D:/fake.pdf")
    errors = ["张三-中方课表.pdf：图片型 PDF 需要 OCR，但 OCR 配置异常：pipeline (OCR) does not exist"]

    records = build_file_records([source], [], errors)

    assert records[0].status == "解析失败"
    assert records[0].text_source == "OCR"
    assert records[0].used_ocr is True
    assert records[0].error is not None
    assert records[0].error.error_type == ErrorType.OCR_CONFIG_FAILED


def test_course_blocks_keep_ocr_text_source():
    blocks = course_blocks_from_legacy(
        [
            {
                "name": "张三",
                "source_type": "中方",
                "week": 3,
                "weekday": "周一",
                "periods": [1],
                "text_source": "OCR",
            }
        ]
    )

    assert blocks[0].text_source == "OCR"

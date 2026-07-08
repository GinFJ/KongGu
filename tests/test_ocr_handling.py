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

from pathlib import Path
from types import SimpleNamespace

import fitz

from core.models import PdfSource
from core.pdf_inspection import inspect_pdf_source


def _text_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Konggu timetable Monday Week 1 course")
    document.save(path)
    document.close()


def _image_pdf(path: Path) -> None:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 300), False)
    image.clear_with(255)
    document = fitz.open()
    page = document.new_page(width=200, height=300)
    page.insert_image(page.rect, stream=image.tobytes("png"))
    document.save(path)
    document.close()


def test_pdf_inspection_normalizes_firecrawl_result_to_one_based_pages(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "成员甲-中方课表.pdf"
    _text_pdf(pdf)

    class FakeInspector:
        @staticmethod
        def detect_pdf(path):
            assert path == str(pdf)
            return SimpleNamespace(
                pdf_type="mixed",
                confidence=0.81234,
                page_count=3,
                pages_needing_ocr=[3, 1, 3],
                ocr_reasons_by_page=[
                    SimpleNamespace(page=3, reasons=["scanned"]),
                    SimpleNamespace(page=1, reasons=["suspected_garbled_text"]),
                ],
                has_encoding_issues=True,
                is_complex_layout=True,
                pages_with_tables=[2],
                pages_with_columns=[3],
                processing_time_ms=17,
            )

    monkeypatch.setattr("core.pdf_inspection._load_pdf_inspector", lambda: FakeInspector)

    result = inspect_pdf_source(PdfSource(pdf.name, "中方", str(pdf), content_hash="abc"))

    assert result.status == "ready"
    assert result.engine == "firecrawl_pdf_inspector"
    assert result.pdf_type == "mixed"
    assert result.confidence == 0.8123
    assert result.pages_needing_ocr == [1, 3]
    assert [item.page for item in result.ocr_reasons_by_page] == [1, 3]
    assert result.has_encoding_issues is True
    assert result.source_hash == "abc"


def test_pdf_inspection_falls_back_to_pymupdf_for_scanned_page(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "成员甲-中方课表.pdf"
    _image_pdf(pdf)

    def unavailable():
        raise ImportError("binding missing")

    monkeypatch.setattr("core.pdf_inspection._load_pdf_inspector", unavailable)

    result = inspect_pdf_source(PdfSource(pdf.name, "中方", str(pdf)))

    assert result.status == "ready"
    assert result.engine == "pymupdf_fallback"
    assert result.pdf_type == "scanned"
    assert result.pages_needing_ocr == [1]
    assert result.ocr_reasons_by_page[0].reasons == ["scanned"]
    assert "回退 PyMuPDF" in result.warning


def test_pdf_inspection_uses_installed_firecrawl_binding(tmp_path: Path):
    pdf = tmp_path / "成员甲-中方课表.pdf"
    _text_pdf(pdf)

    result = inspect_pdf_source(PdfSource(pdf.name, "中方", str(pdf)))

    assert result.status == "ready"
    assert result.engine == "firecrawl_pdf_inspector"
    assert result.engine_version == "0.2.6"
    assert result.pdf_type == "text_based"
    assert result.page_count == 1
    assert result.pages_needing_ocr == []


def test_pdf_inspection_skips_files_over_size_limit(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "large.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr("core.pdf_inspection.MAX_PDF_BYTES", 1)

    result = inspect_pdf_source(PdfSource(pdf.name, "中方", str(pdf)))

    assert result.status == "skipped"
    assert "超过结构检查上限" in result.error


def test_pdf_inspection_returns_privacy_safe_error_for_invalid_pdf(tmp_path: Path):
    pdf = tmp_path / "private-name-成员甲.pdf"
    pdf.write_bytes(b"not a pdf")

    result = inspect_pdf_source(PdfSource(pdf.name, "中方", str(pdf)))

    assert result.status == "unavailable"
    assert str(tmp_path) not in result.error

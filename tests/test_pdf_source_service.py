from pathlib import Path

import pytest

from app.services.pdf_source_service import add_pdf_sources, load_reference_library_summary
from core.models import PdfSource


class FakeKindCore:
    def infer_pdf_kind(self, file_name, path=""):
        if "英方" in file_name:
            return "英方"
        if "中方" in file_name:
            return "中方"
        return ""


def test_add_pdf_sources_infers_kind_and_skips_duplicates(tmp_path: Path):
    chinese = tmp_path / "办公室-张三-中方课表.pdf"
    english = tmp_path / "办公室-张三-英方课表.pdf"
    chinese.write_bytes(b"cn")
    english.write_bytes(b"en")
    existing = [PdfSource(chinese.name, "中方", str(chinese.resolve()))]

    result = add_pdf_sources(
        paths=[str(chinese), str(english)],
        explicit_kind=None,
        existing_sources=existing,
        schedule_core=FakeKindCore(),
    )

    assert len(result.added) == 1
    assert result.added[0].file_name == english.name
    assert result.added[0].kind == "英方"
    assert result.skipped == [str(chinese)]
    assert result.errors == []


def test_add_pdf_sources_uses_explicit_kind_and_defaults_unknown_to_chinese(tmp_path: Path):
    unknown = tmp_path / "张三.pdf"
    explicit = tmp_path / "李四.pdf"
    unknown.write_bytes(b"unknown")
    explicit.write_bytes(b"explicit")

    inferred = add_pdf_sources(
        paths=[str(unknown)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=FakeKindCore(),
    )
    forced = add_pdf_sources(
        paths=[str(explicit)],
        explicit_kind="英方",
        existing_sources=[],
        schedule_core=FakeKindCore(),
    )

    assert inferred.added[0].kind == "中方"
    assert forced.added[0].kind == "英方"


def test_add_pdf_sources_records_missing_paths(tmp_path: Path):
    missing = tmp_path / "missing.pdf"

    result = add_pdf_sources(
        paths=[str(missing)],
        explicit_kind="中方",
        existing_sources=[],
        schedule_core=FakeKindCore(),
    )

    assert result.added == []
    assert result.skipped == [str(missing)]


def test_load_reference_library_summary_counts_pdfs(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"a")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.pdf").write_bytes(b"b")
    (nested / "note.txt").write_text("ignore", encoding="utf-8")

    summary = load_reference_library_summary(str(tmp_path))

    assert summary.path == tmp_path
    assert summary.pdf_count == 2


def test_load_reference_library_summary_raises_for_missing_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_reference_library_summary(str(tmp_path / "missing"))

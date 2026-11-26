from pathlib import Path

import pytest

from app.services.pdf_source_service import add_pdf_sources, load_reference_library_summary
from core import schedule_core as real_schedule_core
from core.models import PdfSource


class FakeKindCore:
    def infer_pdf_kind(self, file_name, path=""):
        if "英方" in file_name:
            return "英方"
        if "中方" in file_name:
            return "中方"
        return ""

    def _extract_name_from_filename(self, file_name):
        stem = Path(file_name).stem
        for token in ("办公室", "外联部", "宣传部", "活动部", "部长", "干事", "中方课表", "英方课表", "课表", "副本"):
            stem = stem.replace(token, "")
        for separator in ("-", "_", " "):
            parts = [part for part in stem.split(separator) if part]
            if len(parts) == 1:
                stem = parts[0]
        return stem.strip("-_ ")


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


def test_add_pdf_sources_skips_same_content_duplicate_files(tmp_path: Path):
    first = tmp_path / "办公室-张三-中方课表.pdf"
    duplicate = tmp_path / "办公室-张三-中方课表-副本.pdf"
    first.write_bytes(b"same-pdf-content")
    duplicate.write_bytes(b"same-pdf-content")

    result = add_pdf_sources(
        paths=[str(first), str(duplicate)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=FakeKindCore(),
    )

    assert [source.file_name for source in result.added] == [first.name]
    assert result.skipped == [str(duplicate)]
    assert result.errors == []


def test_add_pdf_sources_reports_same_content_with_different_member_names(tmp_path: Path):
    first = tmp_path / "活动部-张三-干事-中方课表.pdf"
    duplicate = tmp_path / "活动部-李四-干事-中方课表.pdf"
    first.write_bytes(b"same-pdf-content")
    duplicate.write_bytes(b"same-pdf-content")

    result = add_pdf_sources(
        paths=[str(first), str(duplicate)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=FakeKindCore(),
    )

    assert [source.file_name for source in result.added] == [first.name]
    assert result.skipped == []
    assert len(result.errors) == 1
    assert "成员不一致" in str(result.errors[0][1])
    assert "张三" in str(result.errors[0][1])
    assert "李四" in str(result.errors[0][1])


@pytest.mark.parametrize("second", ["活动部-张三-部长-中方课表.pdf", "活动部-张三-中方课表.pdf"])
def test_same_name_duplicate_requires_consistent_role(tmp_path, second):
    first = tmp_path / "活动部-张三-干事-中方课表.pdf"
    other = tmp_path / second
    first.write_bytes(b"same content")
    other.write_bytes(first.read_bytes())
    result = add_pdf_sources(paths=[str(first),str(other)], explicit_kind=None,
                             existing_sources=[], schedule_core=FakeKindCore())
    assert len(result.errors) == 1 and not result.skipped
    assert first.name in str(result.errors[0][1]) and other.name in str(result.errors[0][1])


def test_add_pdf_sources_rejects_placeholder_member_file_name(tmp_path: Path):
    placeholder = tmp_path / "路人甲_外联部 张三 干事 中方课表.pdf"
    placeholder.write_bytes(b"placeholder")

    result = add_pdf_sources(
        paths=[str(placeholder)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=FakeKindCore(),
    )

    assert result.added == []
    assert result.skipped == []
    assert len(result.errors) == 1
    assert "路人甲" in str(result.errors[0][1])


def test_add_pdf_sources_uses_explicit_kind_and_rejects_unknown_kind(tmp_path: Path):
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

    assert inferred.added == []
    assert len(inferred.errors) == 1
    assert "中方" in str(inferred.errors[0][1])
    assert "英方" in str(inferred.errors[0][1])
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
    assert result.skipped == []
    assert len(result.errors) == 1
    assert result.errors[0][0] == missing
    assert "文件不存在" in str(result.errors[0][1])


def test_add_pdf_sources_keeps_content_hash_without_resident_bytes(tmp_path: Path):
    """A 200 MB batch must not be frozen in memory; the hash is enough to dedupe."""

    import hashlib

    data = b"%PDF-1.4 not a real pdf but content-stable"
    first = tmp_path / "办公室-张三-中方课表.pdf"
    duplicate = tmp_path / "办公室-张三-中方课表-副本.pdf"
    first.write_bytes(data)
    duplicate.write_bytes(data)

    result = add_pdf_sources(
        paths=[str(first), str(duplicate)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=FakeKindCore(),
    )

    assert len(result.added) == 1
    assert result.skipped == [str(duplicate)]
    assert result.added[0].bytes_data is None
    assert result.added[0].content_hash == hashlib.sha256(data).hexdigest()


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


def test_add_pdf_sources_keeps_chinese_kind_inside_english_directory(tmp_path: Path):
    """Directory names must not override the file-name 中方/英方 identity.

    A Chinese timetable stored in a folder such as ``English`` or ``uk`` used to
    be inferred as 英方 and then went through the English/OCR fallback path,
    which could end with "没有识别到可用课表时间块".
    """

    english_dir = tmp_path / "English" / "uk"
    english_dir.mkdir(parents=True)
    chinese = english_dir / "活动部-张三-干事-中方课表.pdf"
    chinese.write_bytes(b"chinese-content")

    result = add_pdf_sources(
        paths=[str(chinese)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=real_schedule_core,
    )

    assert result.errors == []
    assert [source.kind for source in result.added] == ["中方"]


def test_add_pdf_sources_keeps_english_kind_inside_chinese_directory(tmp_path: Path):
    chinese_dir = tmp_path / "中方资料"
    chinese_dir.mkdir()
    english = chinese_dir / "活动部-张三-干事-英方课表.pdf"
    english.write_bytes(b"english-content")

    result = add_pdf_sources(
        paths=[str(english)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=real_schedule_core,
    )

    assert result.errors == []
    assert [source.kind for source in result.added] == ["英方"]


def test_add_pdf_sources_uses_chinese_directory_marker_as_last_resort(tmp_path: Path):
    english_dir = tmp_path / "英方课表"
    english_dir.mkdir()
    unnamed = english_dir / "张三.pdf"
    unnamed.write_bytes(b"unnamed-content")

    result = add_pdf_sources(
        paths=[str(unnamed)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=real_schedule_core,
    )

    assert result.errors == []
    assert [source.kind for source in result.added] == ["英方"]


def test_add_pdf_sources_does_not_guess_from_english_directory_word(tmp_path: Path):
    english_dir = tmp_path / "English"
    english_dir.mkdir()
    unnamed = english_dir / "张三.pdf"
    unnamed.write_bytes(b"unnamed-english-dir")

    result = add_pdf_sources(
        paths=[str(unnamed)],
        explicit_kind=None,
        existing_sources=[],
        schedule_core=real_schedule_core,
    )

    assert result.added == []
    assert len(result.errors) == 1
    assert "无法识别课表类型" in str(result.errors[0][1])

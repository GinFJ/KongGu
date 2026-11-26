"""End-to-end workflow regression with the real parsing core.

Unlike the FakeDesktopCore tests, these PDFs go through the production parser,
quality gate, persistence serializer and Excel writer.  All timetable data is
synthetic, so the fixtures stay inside the repository.
"""

from __future__ import annotations

from pathlib import Path

import fitz
from openpyxl import load_workbook

from app.services.desktop_workflow import (
    export_excel,
    load_workflow_result,
    parse_pdf_paths,
    save_workflow_result,
    serialize_workflow_result,
)
from app.services.review_service import confirm_issue
from app.services.state_store import StateStore
from core import schedule_core


def _isolate_caches(monkeypatch, tmp_path: Path) -> None:
    for name, folder in (
        ("KONGGU_PARSE_CACHE", "parsed_blocks"),
        ("KONGGU_OCR_TEXT_CACHE", "pdf_text"),
        ("KONGGU_OCR_LAYOUT_CACHE", "pdf_layout"),
        ("KONGGU_RASTER_CLIP_CACHE", "pdf_clip"),
    ):
        monkeypatch.setenv(name, str(tmp_path / "cache" / folder))


def _chinese_pdf(path: Path) -> None:
    """One text-layer Chinese week grid: 高等数学 on week 1 Monday periods 1-2."""

    document = fitz.open()
    page = document.new_page()
    y = 72
    lines = ["姓名：张三", "周一 周二 周三 周四 周五 周六 周日", "1周", "03/02", "高等数学"]
    lines.extend(["无"] * 41)
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=11)
        y += 16
    document.save(str(path))
    document.close()


def _english_pdf(path: Path, entries: list[tuple[str, str, str, str]] | None = None) -> None:
    """Eight text-layer English blocks, deliberately not overlapping the Chinese slot."""

    document = fitz.open()
    page = document.new_page()
    y = 72
    entries = entries or [
        ("Tuesday", "2026-03-03", "1", "Physics"),
        ("Wednesday", "2026-03-04", "2", "Chemistry"),
        ("Thursday", "2026-03-05", "3", "Biology"),
        ("Friday", "2026-03-06", "4", "History"),
        ("Monday", "2026-03-09", "5", "Literature"),
        ("Tuesday", "2026-03-10", "6", "Geography"),
        ("Wednesday", "2026-03-11", "7", "Economics"),
        ("Thursday", "2026-03-12", "8", "Philosophy"),
    ]
    for day, date_text, period, course in entries:
        for line in (day, date_text, period, course):
            page.insert_text((72, y), line, fontsize=11)
            y += 14
    document.save(str(path))
    document.close()


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    chinese = tmp_path / "活动部-张三-干事-中方课表.pdf"
    english = tmp_path / "活动部-张三-干事-英方课表.pdf"
    _chinese_pdf(chinese)
    _english_pdf(english)
    return chinese, english


def test_complete_pair_parses_accepted_and_exports_workbook(tmp_path: Path, monkeypatch) -> None:
    _isolate_caches(monkeypatch, tmp_path)
    chinese, english = _pair(tmp_path)

    result = parse_pdf_paths(
        schedule_core=schedule_core,
        paths=[str(chinese), str(english)],
        enforce_quality=True,
    )
    payload = serialize_workflow_result(result, "e2e-accepted")

    assert result.process_result.quality_state == "accepted"
    assert payload["can_export"] is False and payload["verification"]["pending"] > 0
    assert result.process_result.issues == []
    assert len(result.generation_result.blocks) == 10
    assert {record.text_source for record in result.process_result.file_records} == {"内嵌文本"}

    target = tmp_path / "空课结果.xlsx"
    from tests.course_verification_helpers import verify_test_workflow
    result = verify_test_workflow(result, schedule_core, tmp_path)
    assert serialize_workflow_result(result, "e2e-accepted")["can_export"] is True
    exported = export_excel(
        schedule_core=schedule_core,
        workflow_result=result,
        target_path=str(target),
    )

    workbook = load_workbook(exported, read_only=True)
    try:
        assert workbook.sheetnames[:2] == ["第1周", "第2周"]
        assert "空课明细" in workbook.sheetnames
        assert "课程占用明细" in workbook.sheetnames
        occupancy_sheet = workbook["课程占用明细"]
        rows = list(occupancy_sheet.iter_rows(min_row=1, max_row=2, values_only=True))
        header = list(rows[0])
        periods_column = header.index("periods") if "periods" in header else None
        if periods_column is not None:
            assert isinstance(rows[1][periods_column], str)
    finally:
        workbook.close()


def test_missing_second_side_blocks_export(tmp_path: Path, monkeypatch) -> None:
    _isolate_caches(monkeypatch, tmp_path)
    chinese, _ = _pair(tmp_path)

    result = parse_pdf_paths(
        schedule_core=schedule_core,
        paths=[str(chinese)],
        enforce_quality=True,
    )

    assert result.process_result.quality_state == "blocked"
    assert any(issue.code == "MEMBER_SCHEDULE_INCOMPLETE" for issue in result.process_result.issues)
    try:
        export_excel(
            schedule_core=schedule_core,
            workflow_result=result,
            target_path=str(tmp_path / "must-not-export.xlsx"),
        )
    except ValueError as exc:
        assert "不能生成正式多人空课表" in str(exc)
    else:
        raise AssertionError("Incomplete schedule pairs must not export.")


def test_reviewed_conflict_can_be_confirmed_then_exported(tmp_path: Path, monkeypatch) -> None:
    _isolate_caches(monkeypatch, tmp_path)
    chinese = tmp_path / "活动部-张三-干事-中方课表.pdf"
    english = tmp_path / "活动部-张三-干事-英方课表.pdf"
    _chinese_pdf(chinese)
    _english_pdf(
        english,
        [
            ("Monday", "2026-03-02", "1", "Physics"),
            ("Tuesday", "2026-03-03", "2", "Chemistry"),
            ("Wednesday", "2026-03-04", "3", "Biology"),
            ("Thursday", "2026-03-05", "4", "History"),
            ("Monday", "2026-03-09", "5", "Literature"),
            ("Tuesday", "2026-03-10", "6", "Geography"),
            ("Wednesday", "2026-03-11", "7", "Economics"),
            ("Thursday", "2026-03-12", "8", "Philosophy"),
        ],
    )

    result = parse_pdf_paths(
        schedule_core=schedule_core,
        paths=[str(chinese), str(english)],
        enforce_quality=True,
    )
    conflict = next(issue for issue in result.process_result.issues if issue.code == "SCHEDULE_CONFLICT")
    assert result.process_result.quality_state == "needs_review"

    store = StateStore(tmp_path / "state.sqlite3")
    job_id = save_workflow_result(result, store)
    payload = confirm_issue(store, job_id, conflict.issue_id)
    assert payload["quality_state"] == "accepted"

    from tests.course_verification_helpers import verify_test_job
    verify_test_job(store, job_id)
    loaded = load_workflow_result(job_id, store, schedule_core)
    exported = export_excel(
        schedule_core=schedule_core,
        workflow_result=loaded,
        target_path=str(tmp_path / "复核后空课结果.xlsx"),
    )

    assert exported.exists() and exported.stat().st_size > 0

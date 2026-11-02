from pathlib import Path

import fitz
import pandas as pd
import pytest

from app.services.desktop_workflow import (
    export_excel,
    parse_pdf_paths,
    save_workflow_result,
    serialize_workflow_result,
)
from app.services.state_store import StateStore
from app.services.workflow_persistence import snapshot_workflow


class FakeDesktopCore:
    def __init__(self):
        self.export_kwargs = None

    def infer_pdf_kind(self, file_name, path=""):
        if "英方" in file_name:
            return "英方"
        if "中方" in file_name:
            return "中方"
        return ""

    def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None, progress=None):
        blocks = [
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
                "source_file": sources[0]["file_name"],
            }
        ]
        calendar = pd.DataFrame([{"date": "2026-03-16", "week": 3, "weekday": "周一"}])
        return blocks, calendar, [], pd.DataFrame(blocks)

    def synthesize_calendar_from_blocks(self, blocks):
        return pd.DataFrame([{"date": "2026-03-16", "week": 3, "weekday": "周一"}])

    def build_occupancy(self, blocks):
        return {(3, "周一", 1): {"张三"}}

    def build_slot_table(self, occupancy, students, weeks, weekdays, periods):
        return pd.DataFrame([{"week": 3, "free_members": ""}])

    def blocks_to_dataframe(self, blocks):
        return pd.DataFrame(blocks)

    def default_timetable(self):
        return pd.DataFrame([{"period": 1, "start": "08:10", "end": "08:55"}])

    def validate_timetable(self, timetable):
        return timetable, []

    def build_empty_schedule_excel_bytes(self, **kwargs):
        self.export_kwargs = kwargs
        return b"xlsx"


def _minimal_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Konggu timetable Monday Week 1")
    data = document.tobytes()
    document.close()
    return data


@pytest.mark.parametrize('ocr_pages', [[], [1]])
def test_english_image_page_requires_review_even_when_parser_returns_blocks(tmp_path, monkeypatch, ocr_pages):
    pdf = tmp_path / "张三-英方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())
    monkeypatch.setattr(
        "app.services.desktop_workflow.inspect_pdf_sources",
        lambda sources: [{"status": "ready", "page_count": 1,
                          "pages_needing_ocr": ocr_pages, "clipped_pages": []}],
    )
    class FallbackCore(FakeDesktopCore):
        def parse_actual_pdf_sources(self, *args, **kwargs):
            blocks, calendar, errors, frame = super().parse_actual_pdf_sources(*args, **kwargs)
            blocks[0].update(source='英方', source_type='英方', kind='英方', text_source='OCR')
            return blocks, calendar, errors, pd.DataFrame(blocks)
    result = parse_pdf_paths(schedule_core=FallbackCore(), paths=[str(pdf)])
    issues = [i for i in result.process_result.issues if i.code == "ENGLISH_IMAGE_PAGE_REVIEW"]
    assert len(issues) == 1 and issues[0].blocks_export
    assert result.process_result.quality_state != "accepted"


def test_desktop_workflow_parse_serializes_and_exports(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())
    core = FakeDesktopCore()

    result = parse_pdf_paths(schedule_core=core, paths=[str(pdf)])
    payload = serialize_workflow_result(result, "result.pkl")
    snapshot = snapshot_workflow(result)
    exported = export_excel(
        schedule_core=core,
        workflow_result=result,
        target_path=str(tmp_path / "空课结果"),
    )

    assert payload["ok"] is True
    assert payload["summary"]["member_count"] == 1
    assert payload["detected_weeks"] == [3]
    assert payload["detected_week_count"] == 1
    assert payload["detected_max_week"] == 3
    assert payload["members"][0]["member"] == "张三"
    assert payload["availability_preview"][0]["周次"] == 3
    assert payload["details"][0]["inspection_status"] == "ready"
    assert snapshot["generation"]["pdf_inspections"][0]["status"] == "ready"
    assert exported.name == "空课结果.xlsx"
    assert exported.read_bytes() == b"xlsx"


def test_desktop_workflow_blocks_raster_page_with_clipped_timetable(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "成员甲-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())

    class RasterClipCore(FakeDesktopCore):
        def chinese_image_clipped_pages(self, source):
            assert source["path"] == str(pdf)
            return [1]

    monkeypatch.setattr(
        "app.services.desktop_workflow.inspect_pdf_sources",
        lambda sources: [{"status": "ready", "page_count": 1, "pages_needing_ocr": [1], "clipped_pages": []}],
    )

    result = parse_pdf_paths(schedule_core=RasterClipCore(), paths=[str(pdf)])

    assert result.process_result.quality_state == "blocked"
    assert "PDF_PAGE_CONTENT_CLIPPED" in {issue.code for issue in result.process_result.issues}


def test_desktop_workflow_export_can_override_week_count(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())
    core = FakeDesktopCore()
    result = parse_pdf_paths(schedule_core=core, paths=[str(pdf)])

    export_excel(
        schedule_core=core,
        workflow_result=result,
        target_path=str(tmp_path / "空课结果.xlsx"),
        export_week_count=2,
    )

    # 用户要求导出 2 周，但解析到第 3 周仍有课程：已识别的教学周不能被周数设置丢掉。
    assert core.export_kwargs["weeks"] == [1, 2, 3]


def test_export_rejects_result_after_calendar_or_parser_settings_change(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-03-02")
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())
    core = FakeDesktopCore()
    result = parse_pdf_paths(schedule_core=core, paths=[str(pdf)])
    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-10-05")

    with pytest.raises(ValueError, match="重新生成空课表"):
        export_excel(schedule_core=core, workflow_result=result, target_path=str(tmp_path / "stale.xlsx"))
    assert not (tmp_path / "stale.xlsx").exists()


def test_legacy_single_period_is_visible_in_review(tmp_path: Path):
    pdf = tmp_path / "成员甲-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())

    class SinglePeriodCore(FakeDesktopCore):
        def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None, progress=None):
            blocks, calendar, errors, preview = super().parse_actual_pdf_sources(
                sources, uploaded_calendar_df, progress
            )
            del blocks[0]["periods"]
            return blocks, calendar, errors, preview

    result = parse_pdf_paths(schedule_core=SinglePeriodCore(), paths=[str(pdf)])
    assert result.generation_result.blocks[0]["periods"] == [1]
    assert result.process_result.blocks[0].periods == [1]


def test_missing_or_ambiguous_input_blocks_export(tmp_path: Path):
    first = tmp_path / "first" / "成员甲-中方课表.pdf"
    first.parent.mkdir()
    first.write_bytes(_minimal_pdf_bytes())
    missing = tmp_path / "成员乙-英方课表.pdf"
    same_name = tmp_path / "second" / first.name
    same_name.parent.mkdir()
    same_name.write_bytes(_minimal_pdf_bytes() + b"\n% different source")

    for other, expected in ((missing, "文件不存在"), (same_name, "同名课表")):
        result = parse_pdf_paths(
            schedule_core=FakeDesktopCore(),
            paths=[str(first), str(other)],
            enforce_quality=True,
        )
        assert result.process_result.quality_state == "blocked"
        assert any(
            issue.code == "INPUT_PREFLIGHT_FAILED" and expected in issue.message
            for issue in result.process_result.issues
        )


def test_desktop_workflow_rejects_non_pdf(tmp_path: Path):
    note = tmp_path / "note.txt"
    note.write_text("not pdf", encoding="utf-8")

    try:
        parse_pdf_paths(schedule_core=FakeDesktopCore(), paths=[str(note)])
    except ValueError as exc:
        assert "仅支持 PDF 文件" in str(exc)
    else:
        raise AssertionError("Expected non-PDF input to be rejected.")


def test_desktop_workflow_honors_cancellation_after_input_preflight(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())

    try:
        parse_pdf_paths(
            schedule_core=FakeDesktopCore(),
            paths=[str(pdf)],
            cancelled=lambda: True,
        )
    except InterruptedError as exc:
        assert str(exc) == "处理已取消。"
    else:
        raise AssertionError("Cancellation should stop before PDF inspection and parsing.")


def test_desktop_workflow_rejects_unknown_schedule_kind(tmp_path: Path):
    pdf = tmp_path / "张三.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")

    try:
        parse_pdf_paths(schedule_core=FakeDesktopCore(), paths=[str(pdf)])
    except ValueError as exc:
        assert "无法识别课表类型" in str(exc)
        assert "中方" in str(exc)
        assert "英方" in str(exc)
    else:
        raise AssertionError("Expected unknown schedule kind to be rejected.")


def test_desktop_workflow_blocks_invalid_pdf_before_generation(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(b"not-a-pdf")
    core = FakeDesktopCore()

    try:
        parse_pdf_paths(schedule_core=core, paths=[str(pdf)])
    except ValueError as exc:
        assert "安全检查" in str(exc)
    else:
        raise AssertionError("Invalid PDF must be blocked before generation.")


def test_desktop_workflow_rejects_too_many_input_files(tmp_path: Path):
    paths = []
    content = _minimal_pdf_bytes()
    for index in range(33):
        path = tmp_path / f"成员{index}-中方课表.pdf"
        path.write_bytes(content)
        paths.append(str(path))

    try:
        parse_pdf_paths(schedule_core=FakeDesktopCore(), paths=paths)
    except ValueError as exc:
        assert "最多处理 32 份" in str(exc)
    else:
        raise AssertionError("The batch file limit must be enforced.")


def test_desktop_workflow_keeps_input_identity_conflict_as_blocking_issue(tmp_path: Path):
    first = tmp_path / "活动部-张三-干事-中方课表.pdf"
    duplicate = tmp_path / "活动部-李四-干事-中方课表.pdf"
    content = _minimal_pdf_bytes()
    first.write_bytes(content)
    duplicate.write_bytes(content)

    result = parse_pdf_paths(
        schedule_core=FakeDesktopCore(),
        paths=[str(first), str(duplicate)],
        enforce_quality=True,
    )

    assert result.process_result.quality_state == "blocked"
    assert {issue.code for issue in result.process_result.issues} >= {
        "INPUT_PREFLIGHT_FAILED",
        "MEMBER_SCHEDULE_INCOMPLETE",
    }
    try:
        export_excel(
            schedule_core=FakeDesktopCore(),
            workflow_result=result,
            target_path=str(tmp_path / "must-not-export.xlsx"),
        )
    except ValueError as exc:
        assert "不能生成正式多人空课表" in str(exc)
    else:
        raise AssertionError("Input identity conflicts must block export.")


def test_visual_export_mode_is_reserved(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())
    core = FakeDesktopCore()
    result = parse_pdf_paths(schedule_core=core, paths=[str(pdf)])

    try:
        export_excel(schedule_core=core, workflow_result=result, target_path=str(tmp_path / "visual.xlsx"), mode="visual")
    except ValueError as exc:
        assert "visual 导出模式已预留" in str(exc)
    else:
        raise AssertionError("Expected visual export mode to be reserved.")


def test_export_weeks_never_drop_parsed_teaching_weeks():
    from app.services.desktop_workflow import _export_weeks

    assert _export_weeks([1, 2, 16, 17, 18], 16) == list(range(1, 19))
    assert _export_weeks([], 5) == [1, 2, 3, 4, 5]
    assert _export_weeks([3, 4], None) == [3, 4]
    assert _export_weeks([1, 31], 2) == [1, 2]
    assert _export_weeks([1, 2], 0) == [1, 2]
    assert _export_weeks([1, 2], 20) == list(range(1, 21))


def test_week_beyond_teaching_weeks_is_reported_and_blocks_export(tmp_path: Path):
    class OutOfTermCore(FakeDesktopCore):
        def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None, progress=None):
            blocks, calendar, errors, preview = super().parse_actual_pdf_sources(
                sources, uploaded_calendar_df, progress
            )
            blocks[0]["week"] = 20
            blocks[0]["date"] = "2026-07-20"
            return blocks, calendar, errors, preview

    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_minimal_pdf_bytes())
    result = parse_pdf_paths(schedule_core=OutOfTermCore(), paths=[str(pdf)])

    codes = {issue.code for issue in result.process_result.issues}
    assert "WEEK_OUT_OF_SEMESTER" in codes
    assert result.process_result.quality_state != "accepted"


def test_blocked_inspection_file_stays_visible_in_details_and_counts(tmp_path: Path):
    good = tmp_path / "张三-中方课表.pdf"
    good.write_bytes(_minimal_pdf_bytes())
    bad = tmp_path / "李四-英方课表.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Konggu")
    document.save(
        str(bad),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    document.close()

    result = parse_pdf_paths(schedule_core=FakeDesktopCore(), paths=[str(good), str(bad)])

    process = result.process_result
    assert process.summary.source_file_count == 2
    assert process.summary.failed_file_count == 1
    serialized = serialize_workflow_result(result, "job-1")
    assert serialized["summary"]["pdf_count"] == 2
    details = serialized["details"]
    assert len(details) == 2
    failed = [item for item in details if item["status"] == "解析失败"]
    assert len(failed) == 1

    store = StateStore(tmp_path / "state.sqlite3")
    job_id = save_workflow_result(result, store, request={"paths": []})
    job = store.get_job(job_id, include_result=False)
    assert (job["current"], job["total"]) == (2, 2)

from pathlib import Path

import pandas as pd

from app.services.desktop_workflow import export_excel, parse_pdf_paths, serialize_workflow_result
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

    def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None):
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


def test_desktop_workflow_parse_serializes_and_exports(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
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
    assert payload["details"][0]["inspection_status"] == "unavailable"
    assert payload["details"][0]["pdf_type"] == "unknown"
    assert snapshot["generation"]["pdf_inspections"][0]["status"] == "unavailable"
    assert exported.name == "空课结果.xlsx"
    assert exported.read_bytes() == b"xlsx"


def test_desktop_workflow_export_can_override_week_count(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    core = FakeDesktopCore()
    result = parse_pdf_paths(schedule_core=core, paths=[str(pdf)])

    export_excel(
        schedule_core=core,
        workflow_result=result,
        target_path=str(tmp_path / "空课结果.xlsx"),
        export_week_count=2,
    )

    assert core.export_kwargs["weeks"] == [1, 2]


def test_desktop_workflow_rejects_non_pdf(tmp_path: Path):
    note = tmp_path / "note.txt"
    note.write_text("not pdf", encoding="utf-8")

    try:
        parse_pdf_paths(schedule_core=FakeDesktopCore(), paths=[str(note)])
    except ValueError as exc:
        assert "仅支持 PDF 文件" in str(exc)
    else:
        raise AssertionError("Expected non-PDF input to be rejected.")


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


def test_visual_export_mode_is_reserved(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    core = FakeDesktopCore()
    result = parse_pdf_paths(schedule_core=core, paths=[str(pdf)])

    try:
        export_excel(schedule_core=core, workflow_result=result, target_path=str(tmp_path / "visual.xlsx"), mode="visual")
    except ValueError as exc:
        assert "visual 导出模式已预留" in str(exc)
    else:
        raise AssertionError("Expected visual export mode to be reserved.")

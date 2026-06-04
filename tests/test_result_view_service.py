import pandas as pd

from app.services.result_view_service import (
    build_gui_process_result,
    build_member_table,
    build_processing_log_text,
    build_result_log_line,
    build_summary_text,
)
from core.models import AvailabilitySlot, CourseBlock, FileProcessRecord, MemberSchedule, PdfSource


def test_build_member_table_and_summary_text():
    members = [
        MemberSchedule(name="张三", has_chinese=True, has_english=True, status="完整"),
        MemberSchedule(name="李四", has_chinese=True, has_english=False, status="缺英方"),
    ]

    table = build_member_table(members)

    assert list(table.columns) == ["成员", "中方课表", "英方课表", "课程块数", "状态", "风险提示"]
    assert table.iloc[0]["成员"] == "张三"
    assert table.iloc[0]["状态"] == "疑似解析失败"
    assert table.iloc[1]["风险提示"] == "缺少英方课表，请补充上传"
    assert build_summary_text(members, 2) == "共 2 人，完整 1 人，待补 1 人。"
    assert build_result_log_line(2, 12, 3) == "共 2 人，12 个时间块，3 个教学周。"


def test_build_processing_log_text_prefers_errors():
    source = PdfSource("张三-中方.pdf", "中方", "D:/fake.pdf")
    record = FileProcessRecord(source=source, status="解析失败", member_name="张三")

    text = build_processing_log_text(
        errors=["张三-中方.pdf：未识别到课程"],
        preview_df=pd.DataFrame([{"name": "张三"}]),
        file_records=[record],
    )

    assert text.startswith("[ERROR] 张三-中方.pdf：未识别到课程")
    assert "文件处理记录" in text
    assert "[解析失败] 张三-中方.pdf" in text


def test_build_processing_log_text_uses_preview_when_no_errors():
    source = PdfSource("张三-中方.pdf", "中方", "D:/fake.pdf")
    record = FileProcessRecord(source=source, status="已识别", member_name="张三", block_count=1)

    text = build_processing_log_text(
        errors=[],
        preview_df=pd.DataFrame([{"name": "张三", "week": 3}]),
        file_records=[record],
    )

    assert text.startswith("[SUCCESS] 解析成功，无明显错误。")
    assert "文件概览" in text
    assert "张三" in text
    assert "识别到 1 个时间块" in text


def test_build_gui_process_result_summary_and_calendar_rows():
    block = CourseBlock(name="张三", source_type="中方", week=3, weekday="周一", periods=[1])
    member = MemberSchedule(name="张三", has_chinese=True, has_english=False, status="缺英方")
    source = PdfSource("张三-中方.pdf", "中方", "D:/fake.pdf")
    record = FileProcessRecord(source=source, status="已识别", member_name="张三")
    slot = AvailabilitySlot(week=3, date="2026-03-16", weekday="周一", period=1, time_range="08:10-08:55")
    calendar = pd.DataFrame([{"date": "2026-03-16", "week": 3, "weekday": "周一"}])

    result = build_gui_process_result(
        course_blocks=[block],
        members=[member],
        file_records=[record],
        slots=[slot],
        student_count=1,
        block_count=1,
        week_count=1,
        calendar_df=calendar,
    )

    assert result.summary.member_count == 1
    assert result.summary.slot_count == 1
    assert result.logs == ["共 1 人，1 个时间块，1 个教学周。"]
    assert result.calendar_rows == [{"date": "2026-03-16", "week": 3, "weekday": "周一"}]

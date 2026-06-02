from pathlib import Path

from core.errors import ErrorType, ProcessError
from core.logging_config import setup_logging
from core.models import (
    AvailabilitySlot,
    CourseBlock,
    FileProcessRecord,
    MemberSchedule,
    PdfSource,
    ProcessSummary,
    ProcessResult,
)


def test_course_block_and_availability_slot_models():
    block = CourseBlock(
        name="张三",
        source_type="中方",
        week=3,
        weekday="周一",
        periods=[1, 2],
        source_file="sample.pdf",
    )
    assert block.name == "张三"
    assert block.periods == [1, 2]

    slot = AvailabilitySlot(
        week=3,
        date="2026-03-16",
        weekday="周一",
        period=1,
        time_range="08:00-08:45",
        free_members=["李四", "王五"],
        busy_members=["张三"],
    )
    assert slot.free_count == 2
    assert slot.busy_count == 1
    assert slot.free_member_preview == "李四、王五"


def test_process_result_can_collect_members_and_failures():
    source = PdfSource(
        file_name="办公室-张三-中方课表.pdf",
        kind="中方",
        source_path="D:/fake/办公室-张三-中方课表.pdf",
        content_hash="abc123",
    )
    error = ProcessError(
        error_type=ErrorType.CHINESE_PARSE_FAILED,
        message="未识别到周次",
        source_file=source.file_name,
    )
    record = FileProcessRecord(
        source=source,
        status="解析失败",
        detected_kind="中方",
        text_source="OCR",
        text_length=88,
        error=error,
    )
    member = MemberSchedule(name="张三", has_chinese=True, has_english=False, status="缺英方")
    result = ProcessResult(
        members=[member],
        file_records=[record],
        failed_files=[record],
        logs=["导入 1 个 PDF"],
        summary=ProcessSummary(member_count=1, pending_member_count=1, source_file_count=1),
    )

    assert result.members[0].status == "缺英方"
    assert result.members[0].chinese_status == "已导入"
    assert result.members[0].english_status == "未导入"
    assert result.members[0].missing_parts == ["英方"]
    assert result.members[0].display_remark == "缺少英方课表，请补充上传"
    assert result.file_records[0].display_kind == "中方"
    assert result.file_records[0].text_source == "OCR"
    assert result.failed_files[0].error is error
    assert result.summary.source_file_count == 1
    assert "中方课表解析失败" in error.to_user_message()


def test_frontend_granularity_fields_are_available():
    source = PdfSource(file_name="办公室-李四-英方课表.pdf", kind="英方", source_path="D:/fake.pdf")
    record = FileProcessRecord(
        source=source,
        status="已识别",
        member_name="李四",
        text_source="内嵌文本",
        block_count=6,
    )

    assert record.display_kind == "英方"
    assert record.display_result == "识别到 6 个时间块"


def test_setup_logging_creates_logger(tmp_path: Path):
    log_path = tmp_path / "konggu.log"
    logger = setup_logging(log_path)
    logger.info("test log line")
    assert logger.name == "konggu"
    assert log_path.exists()

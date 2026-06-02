"""Shared dataclass models for Konggu schedule processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .errors import ProcessError


ScheduleSourceType = Literal["中方", "英方"]
TextSourceType = Literal["内嵌文本", "OCR", "缓存", "未知"]
ProcessingStatus = Literal["待处理", "识别中", "已识别", "解析失败", "处理完成"]
MemberFileStatus = Literal["未导入", "已导入", "待处理", "解析失败"]
CompletenessStatus = Literal["待处理", "完整", "缺中方", "缺英方", "解析失败"]
FileStatus = Literal[
    "待处理",
    "识别中",
    "已识别",
    "完整",
    "缺中方",
    "缺英方",
    "未导入",
    "已导入",
    "解析失败",
    "处理完成",
]


@dataclass(slots=True)
class PdfSource:
    """A PDF file selected by the user or discovered from a folder."""

    file_name: str
    kind: ScheduleSourceType
    source_path: str
    content_hash: str | None = None
    bytes_data: bytes | None = None


@dataclass(slots=True)
class CourseBlock:
    """One normalized occupied time block for one member."""

    name: str
    source_type: ScheduleSourceType
    week: int
    weekday: str
    periods: list[int]
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    source_file: str | None = None


@dataclass(slots=True)
class MemberSchedule:
    """A member's imported schedules and completeness state."""

    name: str
    has_chinese: bool = False
    has_english: bool = False
    status: CompletenessStatus = "待处理"
    chinese_status: MemberFileStatus = "未导入"
    english_status: MemberFileStatus = "未导入"
    role: str | None = None
    remark: str = ""
    blocks: list[CourseBlock] = field(default_factory=list)
    errors: list[ProcessError] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.has_chinese and self.chinese_status == "未导入":
            self.chinese_status = "已导入"
        if self.has_english and self.english_status == "未导入":
            self.english_status = "已导入"

    @property
    def is_complete(self) -> bool:
        return self.chinese_status == "已导入" and self.english_status == "已导入" and not self.errors

    @property
    def missing_parts(self) -> list[ScheduleSourceType]:
        missing: list[ScheduleSourceType] = []
        if self.chinese_status == "未导入":
            missing.append("中方")
        if self.english_status == "未导入":
            missing.append("英方")
        return missing

    @property
    def display_remark(self) -> str:
        if self.remark:
            return self.remark
        if self.errors:
            return "解析失败，请检查 PDF 是否清晰或重新上传"
        if self.missing_parts:
            return f"缺少{'、'.join(self.missing_parts)}课表，请补充上传"
        if self.is_complete:
            return "可生成空课表"
        return "等待识别结果"


@dataclass(slots=True)
class AvailabilitySlot:
    """One row in the generated empty schedule."""

    week: int
    date: str
    weekday: str
    period: int
    time_range: str
    free_members: list[str] = field(default_factory=list)
    busy_members: list[str] = field(default_factory=list)

    @property
    def free_count(self) -> int:
        return len(self.free_members)

    @property
    def busy_count(self) -> int:
        return len(self.busy_members)

    @property
    def free_member_preview(self) -> str:
        return "、".join(self.free_members[:3])

    @property
    def busy_member_preview(self) -> str:
        return "、".join(self.busy_members[:3])


@dataclass(slots=True)
class FileProcessRecord:
    """Processing state and diagnostics for one source file."""

    source: PdfSource
    status: ProcessingStatus = "待处理"
    member_name: str | None = None
    detected_kind: ScheduleSourceType | None = None
    text_source: TextSourceType = "未知"
    text_length: int = 0
    used_ocr: bool = False
    block_count: int = 0
    result_message: str = ""
    error: ProcessError | None = None

    @property
    def display_kind(self) -> ScheduleSourceType:
        return self.detected_kind or self.source.kind

    @property
    def display_result(self) -> str:
        if self.result_message:
            return self.result_message
        if self.error:
            return self.error.to_user_message()
        if self.status == "已识别":
            return f"识别到 {self.block_count} 个时间块"
        if self.status == "识别中":
            return "正在读取 PDF 文本 / OCR 识别"
        return "等待批量解析"


@dataclass(slots=True)
class ProcessSummary:
    """Numbers shown by the workbench overview and export confirmation."""

    member_count: int = 0
    complete_member_count: int = 0
    pending_member_count: int = 0
    failed_file_count: int = 0
    source_file_count: int = 0
    slot_count: int = 0


@dataclass(slots=True)
class ProcessResult:
    """Output object returned by the full processing pipeline."""

    blocks: list[CourseBlock] = field(default_factory=list)
    members: list[MemberSchedule] = field(default_factory=list)
    file_records: list[FileProcessRecord] = field(default_factory=list)
    slots: list[AvailabilitySlot] = field(default_factory=list)
    failed_files: list[FileProcessRecord] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    calendar_rows: list[dict] = field(default_factory=list)
    summary: ProcessSummary = field(default_factory=ProcessSummary)

"""Shared dataclass models for Konggu schedule processing."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Literal

from .errors import ProcessError


ScheduleSourceType = Literal["中方", "英方"]
TextSourceType = Literal["内嵌文本", "OCR", "缓存", "未知"]
ProcessingStatus = Literal["待处理", "识别中", "已识别", "解析失败", "处理完成"]
MemberFileStatus = Literal["未导入", "已导入", "待处理", "解析失败"]
CompletenessStatus = Literal["待处理", "完整", "缺中方", "缺英方", "解析失败"]
QualityState = Literal["accepted", "needs_review", "blocked"]
IssueSeverity = Literal["info", "warning", "error"]
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


@dataclass(slots=True, frozen=True)
class MemberIdentity:
    """Stable local identity used when names alone are not unique."""

    name: str
    department: str | None = None
    role: str | None = None
    display_suffix: str | None = None

    @property
    def member_key(self) -> str:
        parts = [self.name.strip(), (self.department or "").strip(), (self.role or "").strip()]
        key = "｜".join(parts)
        if self.display_suffix:
            key = f"{key}｜{self.display_suffix.strip()}"
        return key

    @property
    def confirmation_required(self) -> bool:
        return not bool(self.name.strip() and (self.department or "").strip() and (self.role or "").strip())

    @property
    def display_name(self) -> str:
        details = " · ".join(item for item in (self.department, self.role, self.display_suffix) if item)
        return f"{self.name}（{details}）" if details else self.name


@dataclass(slots=True)
class OcrToken:
    """One normalized OCR token with enough geometry for PDF review overlays."""

    page: int
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None
    polygon: list[tuple[float, float]] = field(default_factory=list)
    engine: str = "paddle_v4"
    model_version: str = "PP-OCRv4-mobile"
    page_width: float = 0
    page_height: float = 0
    elapsed_ms: int = 0


@dataclass(slots=True)
class ParseIssue:
    """A user-visible parsing or quality issue."""

    code: str
    message: str
    severity: IssueSeverity = "warning"
    field: str | None = None
    source_hash: str | None = None
    source_file: str | None = None
    block_id: str | None = None
    suggestion: str = ""
    blocks_export: bool = False
    confirmed: bool = False
    issue_id: str | None = None

    def __post_init__(self) -> None:
        if not self.issue_id:
            raw = "|".join(
                [
                    self.code,
                    self.source_hash or "",
                    self.source_file or "",
                    self.block_id or "",
                    self.field or "",
                    self.message,
                ]
            )
            self.issue_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


@dataclass(slots=True)
class CorrectionRecord:
    """A durable human correction tied to a source and parser signature."""

    source_hash: str
    block_id: str
    field: str
    original_value: Any
    new_value: Any
    reason: str
    parser_signature: str
    operator_id: str = "本机用户"
    created_at: str = ""
    stale: bool = False


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
    course: str | None = None
    source_file: str | None = None
    text_source: TextSourceType = "未知"
    block_id: str = ""
    member_key: str = ""
    department: str | None = None
    role: str | None = None
    source_hash: str | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.member_key:
            identity = MemberIdentity(self.name, self.department, self.role)
            self.member_key = self.name if identity.confirmation_required else identity.member_key
        if not self.block_id:
            stable = {
                "member_key": self.member_key,
                "week": self.week,
                "weekday": self.weekday,
                "periods": sorted(int(period) for period in self.periods),
                "course": self.course or "",
                "source_hash": self.source_hash or "",
                "source_file": self.source_file or "",
                "page": self.page,
                "bbox": self.bbox,
            }
            self.block_id = hashlib.sha256(
                json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:24]


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
    department: str | None = None
    member_key: str = ""
    display_suffix: str | None = None
    remark: str = ""
    blocks: list[CourseBlock] = field(default_factory=list)
    errors: list[ProcessError] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.member_key:
            identity = MemberIdentity(
                self.name,
                self.department,
                self.role,
                self.display_suffix,
            )
            self.member_key = self.name if identity.confirmation_required else identity.member_key
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
            first_error = self.errors[0]
            if hasattr(first_error, "to_user_message"):
                return first_error.to_user_message()
            return str(first_error)
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
    source_hash: str | None = None
    layout_profile: str | None = None
    quality_state: QualityState = "accepted"
    issues: list[ParseIssue] = field(default_factory=list)
    duration_ms: int = 0

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
    quality_state: QualityState = "accepted"
    issues: list[ParseIssue] = field(default_factory=list)
    corrections: list[CorrectionRecord] = field(default_factory=list)
    parser_signature: str = ""
    job_id: str = ""

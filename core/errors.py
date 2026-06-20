"""Structured error types used by the Konggu processing pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorType(str, Enum):
    """Known processing error categories.

    The Chinese values are kept user-facing so API and Web UI layers can display
    them directly without maintaining a second mapping table.
    """

    FILE_READ_FAILED = "文件读取失败"
    PDF_TEXT_EXTRACT_FAILED = "PDF 文本提取失败"
    OCR_FAILED = "OCR 识别失败"
    SCHEDULE_TYPE_UNKNOWN = "课表类型识别失败"
    NAME_RECOGNITION_FAILED = "姓名识别失败"
    CHINESE_PARSE_FAILED = "中方课表解析失败"
    ENGLISH_DATE_MAPPING_FAILED = "英方日期映射失败"
    PERIOD_MAPPING_FAILED = "节次映射失败"
    EXCEL_EXPORT_FAILED = "Excel 导出失败"


@dataclass(slots=True)
class ProcessError:
    """A structured error tied to one file or one processing stage."""

    error_type: ErrorType
    message: str
    source_file: str | None = None
    detail: str | None = None

    def to_user_message(self) -> str:
        """Return a compact message suitable for user-facing status areas."""

        prefix = f"[{self.error_type.value}]"
        if self.source_file:
            return f"{prefix} {self.source_file}: {self.message}"
        return f"{prefix} {self.message}"

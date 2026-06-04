"""View data helpers for displaying parsed schedule results."""

from __future__ import annotations

import pandas as pd

from core.legacy_adapter import build_process_result, infer_member_name_from_filename
from core.models import AvailabilitySlot, FileProcessRecord, MemberSchedule, PdfSource, ProcessResult


def build_member_table(members: list[MemberSchedule]) -> pd.DataFrame:
    """Build the member completeness table shown in the GUI."""

    rows = []
    for member in members:
        block_count = len(member.blocks)
        rows.append(
            {
                "成员": member.name,
                "中方课表": member.chinese_status,
                "英方课表": member.english_status,
                "课程块数": block_count,
                "状态": _member_health_status(member, block_count),
                "风险提示": member.display_remark,
            }
        )
    return pd.DataFrame(rows)


def build_detail_table(file_records: list[FileProcessRecord]) -> pd.DataFrame:
    """Build the recognition detail table shown in the GUI."""

    rows = []
    for record in file_records:
        rows.append(
            {
                "文件名": record.source.file_name,
                "成员": record.member_name or "未识别",
                "类型": record.display_kind,
                "解析器": record.text_source,
                "缓存": "未知",
                "课程块数量": record.block_count,
                "警告信息": record.display_result,
            }
        )
    return pd.DataFrame(rows)


def build_file_table(sources: list[PdfSource], file_records: list[FileProcessRecord]) -> pd.DataFrame:
    """Build the imported file overview table."""

    records_by_source = {record.source.file_name: record for record in file_records}
    rows = []
    for source in sources:
        record = records_by_source.get(source.file_name)
        member_name = record.member_name if record and record.member_name else infer_member_name_from_filename(source.file_name)
        rows.append(
            {
                "文件名": source.file_name,
                "推断成员": member_name or "未识别",
                "类型": record.display_kind if record else source.kind,
                "状态": record.status if record else "等待",
                "课程块数": record.block_count if record else 0,
                "备注": record.display_result if record else "等待解析",
            }
        )
    return pd.DataFrame(rows)


def _member_health_status(member: MemberSchedule, block_count: int) -> str:
    if block_count == 0:
        return "疑似解析失败"
    if member.status == "完整":
        return "正常"
    if member.status == "缺中方":
        return "缺少中方课表"
    if member.status == "缺英方":
        return "缺少英方课表"
    if member.errors:
        return "需检查"
    return member.status


def build_summary_text(members: list[MemberSchedule], student_count: int) -> str:
    """Build the short parse-completion summary for the sidebar."""

    complete_members = sum(1 for member in members if member.status == "完整")
    pending_members = max(0, len(members) - complete_members)
    return f"共 {student_count} 人，完整 {complete_members} 人，待补 {pending_members} 人。"


def build_result_log_line(student_count: int, block_count: int, week_count: int) -> str:
    """Build the compact process-result log summary."""

    return f"共 {student_count} 人，{block_count} 个时间块，{week_count} 个教学周。"


def build_processing_log_text(
    *,
    errors: list[str],
    preview_df: pd.DataFrame,
    file_records: list[FileProcessRecord],
) -> str:
    """Build the processing log tab text."""

    record_lines = []
    for record in file_records:
        member = record.member_name or "未识别成员"
        record_lines.append(
            f"[{record.status}] {record.source.file_name}｜{record.display_kind}｜{member}｜{record.display_result}"
        )

    if errors:
        text = "\n".join(f"[ERROR] {error}" for error in errors)
        if record_lines:
            text += "\n\n文件处理记录：\n" + "\n".join(record_lines)
        return text

    if preview_df is not None and not preview_df.empty:
        text = "[SUCCESS] 解析成功，无明显错误。\n\n文件概览：\n" + preview_df.to_string(index=False)
        if record_lines:
            text += "\n\n文件处理记录：\n" + "\n".join(record_lines)
        return text

    text = "[SUCCESS] 解析成功，无明显错误。"
    if record_lines:
        text += "\n\n文件处理记录：\n" + "\n".join(record_lines)
    return text


def build_gui_process_result(
    *,
    course_blocks,
    members: list[MemberSchedule],
    file_records: list[FileProcessRecord],
    slots: list[AvailabilitySlot],
    student_count: int,
    block_count: int,
    week_count: int,
    calendar_df: pd.DataFrame,
) -> ProcessResult:
    """Build the ProcessResult retained by the GUI for export and status displays."""

    return build_process_result(
        blocks=course_blocks,
        members=members,
        file_records=file_records,
        slots=slots,
        logs=[build_result_log_line(student_count, block_count, week_count)],
        calendar_rows=calendar_df.to_dict("records") if calendar_df is not None and not calendar_df.empty else [],
    )

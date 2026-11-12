"""Deterministic quality gates for parse, review and export."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from .models import FileProcessRecord, MemberSchedule, ParseIssue, QualityState


SCHEDULE_RANGE_SUGGESTION = "请对照原课表核对左侧的第一周开始日期和教学周数，保存设置后重新生成空课表。"


def _parsed_date_range(blocks: Iterable[dict[str, Any]]) -> str:
    dates = set()
    for block in blocks:
        try:
            dates.add(date.fromisoformat(str(block.get("date"))))
        except ValueError:
            continue
    if not dates:
        return ""
    first, last = min(dates), max(dates)
    span = first.isoformat() if first == last else f"{first} 至 {last}"
    return f"按当前设置解析的越界课程日期为 {span}。"


def schedule_range_violations(
    *,
    blocks: Iterable[dict[str, Any]],
    calendar_weeks: Iterable[int],
    earliest_calendar_date: str | None,
) -> list[tuple[str, str]]:
    """Return ``(code, message)`` pairs for blocks outside the current semester.

    Shared by the parse path and the review/replay path so one edit can never
    pass the gate while the same data fails it after a correction.
    """

    block_list = list(blocks)
    known_weeks = {int(week) for week in calendar_weeks}
    problems: list[tuple[str, str]] = []
    if known_weeks:
        outside = [
            block for block in block_list
            if block.get("week") is not None and int(block["week"]) not in known_weeks
        ]
        if outside:
            weeks = sorted({int(block["week"]) for block in outside})
            start = f"从 {earliest_calendar_date} 开始，" if earliest_calendar_date else ""
            problems.append(
                (
                    "WEEK_OUT_OF_SEMESTER",
                    f"课表包含第 {'、'.join(map(str, weeks))} 周课程，超出当前学期教学周设置。"
                    f"当前日历{start}覆盖第 {min(known_weeks)} 至 {max(known_weeks)} 周。"
                    f"{_parsed_date_range(outside)}",
                )
            )
    if earliest_calendar_date:
        early = [
            block
            for block in block_list
            if str(block.get("date") or "") and str(block["date"]) < earliest_calendar_date
        ]
        if early:
            problems.append(
                (
                    "PRE_TERM_DATE",
                    f"课程日期早于当前日历起点 {earliest_calendar_date}。{_parsed_date_range(early)}",
                )
            )
    return problems


def evaluate_quality(
    *,
    file_records: Iterable[FileProcessRecord],
    members: Iterable[MemberSchedule],
    inherited_issues: Iterable[ParseIssue] = (),
    enforce_identity: bool = True,
) -> tuple[QualityState, list[ParseIssue]]:
    issues = list(inherited_issues)
    records = list(file_records)
    member_rows = list(members)

    for record in records:
        source_hash = record.source.content_hash or record.source_hash
        if record.error or record.status == "解析失败":
            issues.append(
                ParseIssue(
                    code="FILE_PARSE_FAILED",
                    message=record.display_result or "课表解析失败",
                    severity="error",
                    source_hash=source_hash,
                    source_file=record.source.file_name,
                    suggestion="检查文件是否选错、损坏或需要 OCR；修复后仅重试失败文件。",
                    blocks_export=True,
                )
            )
        elif record.block_count == 0:
            issues.append(
                ParseIssue(
                    code="NO_COURSE_BLOCKS",
                    message="未识别到任何课程占用槽",
                    severity="error",
                    source_hash=source_hash,
                    source_file=record.source.file_name,
                    suggestion="在 PDF 复核页检查文本层、OCR 框和课表类型。",
                    blocks_export=True,
                )
            )

    for member in member_rows:
        if enforce_identity and member.has_chinese != member.has_english:
            missing = "英方" if member.has_chinese else "中方"
            issues.append(
                ParseIssue(
                    code="MEMBER_SCHEDULE_INCOMPLETE",
                    message=f"{member.name} 缺少{missing}课表，不能可靠计算空闲时间",
                    severity="error",
                    field="schedule_pair",
                    suggestion=f"补充该成员的{missing}课表后重新生成空课表。",
                    blocks_export=True,
                )
            )
        if enforce_identity and (not member.department or not member.role):
            issues.append(
                ParseIssue(
                    code="MEMBER_IDENTITY_INCOMPLETE",
                    message=f"{member.name} 缺少部门或角色，无法形成可靠组合标识",
                    severity="warning",
                    field="member_key",
                    suggestion="补充部门和角色后确认。",
                    blocks_export=True,
                )
            )
        if member.errors:
            issues.append(
                ParseIssue(
                    code="SCHEDULE_CONFLICT",
                    message=member.display_remark,
                    severity="warning",
                    field="periods",
                    suggestion="核对冲突节次对应的中方和英方课表。",
                    blocks_export=True,
                )
            )

    deduped: dict[str, ParseIssue] = {}
    for issue in issues:
        deduped[str(issue.issue_id)] = issue
    final_issues = list(deduped.values())
    return quality_state(final_issues), final_issues


def quality_state(issues: Iterable[ParseIssue]) -> QualityState:
    active = [issue for issue in issues if not issue.confirmed]
    if any(issue.severity == "error" and issue.blocks_export for issue in active):
        return "blocked"
    if any(issue.blocks_export for issue in active):
        return "needs_review"
    return "accepted"


def assert_export_allowed(state: QualityState, issues: Iterable[ParseIssue]) -> None:
    if state == "accepted":
        return
    active = [issue for issue in issues if not issue.confirmed and issue.blocks_export]
    details = "；".join(issue.message for issue in active[:5])
    label = "存在阻断错误" if state == "blocked" else "仍有未确认问题"
    raise ValueError(f"{label}，不能生成正式多人空课表。{details}")

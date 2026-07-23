"""Deterministic quality gates for parse, review and export."""

from __future__ import annotations

from collections.abc import Iterable

from .models import FileProcessRecord, MemberSchedule, ParseIssue, QualityState


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

"""Human review, correction reuse and quality-state updates."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from app.services.state_store import StateStore, utc_now
from app.services.availability_preview_service import build_availability_preview, build_date_map
from app.services.result_view_service import build_gui_process_result
from app.services.workflow_persistence import restore_workflow, snapshot_workflow
from core.legacy_adapter import build_file_records, build_member_schedules, course_blocks_from_legacy
from core.models import CorrectionRecord, ParseIssue
from core.quality import evaluate_quality, quality_state


EDITABLE_BLOCK_FIELDS = {
    "course",
    "week",
    "weekday",
    "periods",
    "member_key",
    "name",
    "department",
    "role",
}
DERIVED_ISSUE_CODES = {
    "FILE_PARSE_FAILED",
    "NO_COURSE_BLOCKS",
    "MEMBER_SCHEDULE_INCOMPLETE",
    "MEMBER_IDENTITY_INCOMPLETE",
    "SCHEDULE_CONFLICT",
}


def get_review_payload(store: StateStore, job_id: str) -> dict[str, Any]:
    job = store.get_job(job_id, include_sensitive_paths=True)
    if not job or not job.get("result"):
        raise ValueError("未找到可复核的解析任务。")
    result = dict(job["result"])
    generation = dict(result.get("generation") or {})
    signature = str(result.get("parser_signature") or job.get("parser_signature") or "")
    source_hashes = {
        str(item.get("content_hash") or "")
        for item in generation.get("sources", [])
        if item.get("content_hash")
    }
    corrections = []
    for source_hash in sorted(source_hashes):
        store.mark_other_signatures_stale(source_hash, signature)
        corrections.extend(store.corrections_for_source(source_hash, signature))
    issues = job.get("issues", [])
    sources = [dict(item) for item in generation.get("sources", [])]
    session_paths = {
        (str(item.get("file_name") or ""), str(item.get("source_hash") or "")): str(item.get("source_path") or "")
        for item in job.get("files", [])
        if item.get("source_path")
    }
    for source in sources:
        source["source_path"] = session_paths.get(
            (str(source.get("file_name") or ""), str(source.get("content_hash") or "")), ""
        )
    return {
        "ok": True,
        "job_id": job_id,
        "quality_state": job.get("quality_state"),
        "parser_signature": signature,
        "sources": sources,
        "inspections": generation.get("pdf_inspections", []),
        "blocks": generation.get("blocks", []),
        "issues": issues,
        "corrections": corrections,
    }


def apply_correction(
    *,
    store: StateStore,
    schedule_core: Any,
    job_id: str,
    block_id: str,
    field: str,
    new_value: Any,
    reason: str,
    operator_id: str,
) -> dict[str, Any]:
    if field not in EDITABLE_BLOCK_FIELDS:
        raise ValueError(f"字段不可修改：{field}")
    if not reason.strip():
        raise ValueError("请填写修正原因。")
    job = store.get_job(job_id)
    if not job or not job.get("result"):
        raise ValueError("未找到可修正的解析任务。")
    snapshot = dict(job["result"])
    blocks = list((snapshot.get("generation") or {}).get("blocks", []))
    target = next((block for block in blocks if str(block.get("block_id")) == block_id), None)
    if target is None:
        raise ValueError("未找到要修正的课程块。")
    original_value = target.get(field)
    normalized = _normalize_value(field, new_value)
    target[field] = normalized
    if field == "periods":
        target["period"] = normalized[0] if normalized else None
    signature = str(snapshot.get("parser_signature") or job.get("parser_signature") or "")
    source_hash = str(target.get("source_hash") or "")
    correction = CorrectionRecord(
        source_hash=source_hash,
        block_id=block_id,
        field=field,
        original_value=original_value,
        new_value=normalized,
        reason=reason.strip(),
        parser_signature=signature,
        operator_id=operator_id.strip() or "本机用户",
        created_at=utc_now(),
    )
    snapshot.setdefault("corrections", []).append(asdict(correction))
    workflow = restore_workflow(snapshot, schedule_core)
    _refresh_derived(workflow, schedule_core)
    refreshed = snapshot_workflow(workflow)
    # The correction row, the refreshed snapshot and its issues must land together:
    # a failed save must not leave a correction that no review snapshot reflects.
    with store.connect() as connection:
        correction_id = store.add_correction(
            source_hash=source_hash,
            block_id=block_id,
            field=field,
            original_value=original_value,
            new_value=normalized,
            reason=reason.strip(),
            parser_signature=signature,
            operator_id=operator_id.strip() or "本机用户",
            job_id=job_id,
            connection=connection,
        )
        store.save_job_result(
            job_id,
            result_json=json.dumps(refreshed, ensure_ascii=False, separators=(",", ":")),
            error=str(job.get("error") or ""),
            quality_state=refreshed["quality_state"],
            current=int(job.get("current") or 0),
            total=int(job.get("total") or 0),
            issues=refreshed["issues"],
            connection=connection,
        )
    return {"ok": True, "correction_id": correction_id, **get_review_payload(store, job_id)}


def confirm_issue(store: StateStore, job_id: str, issue_id: str) -> dict[str, Any]:
    job = store.get_job(job_id)
    if not job or not job.get("result"):
        raise ValueError("未找到可确认的解析任务。")
    target = next(
        (item for item in snapshot_issues(job["result"]) if str(item.get("issue_id")) == issue_id),
        None,
    )
    if target is None:
        raise ValueError("未找到要确认的问题。")
    if target.get("severity") == "error":
        raise ValueError("阻断错误不能仅靠确认放行，必须修复源文件或解析结果。")
    snapshot = dict(job["result"])
    issues = []
    for item in snapshot.get("issues", []):
        issue = dict(item)
        if str(issue.get("issue_id")) == issue_id:
            issue["confirmed"] = True
        issues.append(issue)
    parsed_issues = [ParseIssue(**item) for item in issues]
    state = quality_state(parsed_issues)
    snapshot["issues"] = issues
    snapshot["quality_state"] = state
    # The confirmed flag and the snapshot that records it must commit together.
    with store.connect() as connection:
        store.confirm_issue(job_id, issue_id, connection=connection)
        store.update_job(
            job_id,
            result_json=json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            quality_state=state,
            connection=connection,
        )
    return {"ok": True, **get_review_payload(store, job_id)}


def apply_saved_corrections(workflow: Any, store: StateStore, schedule_core: Any) -> int:
    """Reuse only corrections from the exact same parser signature."""

    signature = workflow.process_result.parser_signature
    blocks = workflow.generation_result.blocks
    applied = 0
    for source in workflow.generation_result.model_sources:
        if not source.content_hash:
            continue
        store.mark_other_signatures_stale(source.content_hash, signature)
        for correction in store.corrections_for_source(source.content_hash, signature):
            if correction["stale"]:
                continue
            target = next(
                (item for item in blocks if str(item.get("block_id")) == correction["block_id"]),
                None,
            )
            if target is None or correction["field"] not in EDITABLE_BLOCK_FIELDS:
                continue
            target[correction["field"]] = correction["new_value"]
            if correction["field"] == "periods":
                target["period"] = correction["new_value"][0] if correction["new_value"] else None
            workflow.process_result.corrections.append(
                CorrectionRecord(
                    source_hash=correction["source_hash"],
                    block_id=correction["block_id"],
                    field=correction["field"],
                    original_value=correction["original_value"],
                    new_value=correction["new_value"],
                    reason=correction["reason"],
                    parser_signature=correction["parser_signature"],
                    operator_id=correction["operator_id"],
                    created_at=correction["created_at"],
                    stale=False,
                )
            )
            applied += 1
    if applied:
        _refresh_derived(workflow, schedule_core)
    return applied


def _normalize_value(field: str, value: Any) -> Any:
    if field == "week":
        week = int(value)
        if not 1 <= week <= 30:
            raise ValueError("周次必须在 1 到 30 之间。")
        return week
    if field == "weekday":
        text = str(value)
        if text not in {"周一", "周二", "周三", "周四", "周五", "周六", "周日"}:
            raise ValueError("星期值无效。")
        return text
    if field == "periods":
        raw = value if isinstance(value, list) else [value]
        periods = sorted({int(item) for item in raw})
        allowed = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
        if not periods or not set(periods).issubset(allowed):
            raise ValueError("节次必须吸附到已配置的合法节次。")
        return periods
    return str(value).strip()


def _refresh_derived(workflow: Any, schedule_core: Any) -> None:
    generation = workflow.generation_result
    generation.students = sorted({
        str(block.get("member_key") or block.get("name"))
        for block in generation.blocks
        if block.get("member_key") or block.get("name")
    })
    calendar_weeks: set[int] = set()
    if generation.calendar_df is not None and "week" in generation.calendar_df:
        for value in generation.calendar_df["week"]:
            try:
                calendar_weeks.add(int(value))
            except (TypeError, ValueError):
                continue
    generation.weeks = sorted(calendar_weeks | {
        int(block["week"]) for block in generation.blocks if block.get("week") is not None
    })
    date_map = build_date_map(generation.calendar_df)
    for block in generation.blocks:
        block["date"] = date_map.get((int(block["week"]), str(block["weekday"])), "")
    generation.course_blocks = course_blocks_from_legacy(generation.blocks)
    generation.member_schedules = build_member_schedules(generation.course_blocks, generation.students)
    prior_records = {record.source.content_hash: record for record in generation.file_records}
    generation.file_records = build_file_records(
        generation.model_sources, generation.course_blocks, generation.errors
    )
    for record in generation.file_records:
        previous = prior_records.get(record.source.content_hash)
        if previous:
            record.layout_profile = previous.layout_profile
    generation.occupancy = schedule_core.build_occupancy(generation.blocks)
    generation.blocks_df = schedule_core.blocks_to_dataframe(generation.blocks)
    timetable = schedule_core.default_timetable()
    timetable, _ = schedule_core.validate_timetable(timetable)
    periods = [int(period) for period in timetable["period"]]
    generation.all_slot_df = schedule_core.build_slot_table(
        generation.occupancy,
        generation.students,
        generation.weeks,
        ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        periods,
    )
    preview = build_availability_preview(
        occupancy=generation.occupancy,
        students=generation.students,
        weeks=generation.weeks,
        weekdays=["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        calendar_df=generation.calendar_df,
        timetable_df=timetable,
    )
    workflow.preview_df = preview.free_df
    previous_process = workflow.process_result
    process = build_gui_process_result(
        course_blocks=generation.course_blocks,
        members=generation.member_schedules,
        file_records=generation.file_records,
        slots=preview.slots,
        student_count=len(generation.students),
        block_count=len(generation.blocks),
        week_count=len(generation.weeks),
        calendar_df=generation.calendar_df,
    )
    retained_issues = [
        issue for issue in previous_process.issues if issue.code not in DERIVED_ISSUE_CODES
    ]
    _, issues = evaluate_quality(
        file_records=generation.file_records,
        members=generation.member_schedules,
        inherited_issues=retained_issues,
        enforce_identity=True,
    )
    confirmed_ids = {issue.issue_id for issue in previous_process.issues if issue.confirmed}
    for issue in issues:
        issue.confirmed = issue.issue_id in confirmed_ids
    process.issues = issues
    process.quality_state = quality_state(issues)
    process.corrections = previous_process.corrections
    process.parser_signature = previous_process.parser_signature
    process.job_id = previous_process.job_id
    workflow.process_result = process


def snapshot_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in snapshot.get("issues", [])]

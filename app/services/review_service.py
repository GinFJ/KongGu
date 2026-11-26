"""Human review, correction reuse and quality-state updates."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from app.services.state_store import StateStore, utc_now
from app.services.course_verification import review_blocks, verification_queue
from app.services.review_operations import lock_review_revision, review_revision
from app.services.availability_preview_service import align_slot_dates, build_availability_preview, build_date_map
from app.services.result_view_service import build_gui_process_result
from app.services.workflow_persistence import restore_workflow, snapshot_workflow
from core.legacy_adapter import build_file_records, build_member_schedules, course_blocks_from_legacy
from core.models import CorrectionRecord, MemberIdentity, ParseIssue
from core.quality import SCHEDULE_RANGE_SUGGESTION, evaluate_quality, quality_state, schedule_range_violations


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
    "ENGLISH_CELL_TEXT_INCOMPLETE",
    "FILE_PARSE_FAILED",
    "NO_COURSE_BLOCKS",
    "MEMBER_SCHEDULE_INCOMPLETE",
    "MEMBER_IDENTITY_INCOMPLETE",
    "SCHEDULE_CONFLICT",
    # 修正可能把课程改出/改回学期范围：这两类问题必须在修正后重算，
    # 否则门禁可被绕过（改到第 19 周仍 accepted）或永久卡死（改回来仍 blocked）。
    "WEEK_OUT_OF_SEMESTER",
    "PRE_TERM_DATE",
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
        # 只读接口不得写库；stale 由 corrections_for_source 按当前签名动态判定，
        # 旧签名修正不能因此被永久标记（签名恢复后仍应能复用）。
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
    from app.services.review_operations import region_choices
    from app.services.date_review import date_review_view
    blocks = review_blocks(generation.get("blocks", []), generation.get("pdf_inspections", []))
    removed_blocks = generation.get("removed_blocks", [])
    if result.get("date_review"):
        calendar = {(int(row['week']), row['weekday']): str(row['date'])[:10]
                    for row in generation['calendar_rows']}
        def dated(row):
            actual = calendar.get((row.get('week'), row.get('weekday')))
            return {**row, 'date': actual} if actual else row
        blocks = [dated(row) for row in blocks]
        removed_blocks = [dated(row) for row in review_blocks(removed_blocks, generation.get("pdf_inspections", []))]
    return {
        "ok": True,
        "job_id": job_id,
        "quality_state": job.get("quality_state"),
        "parser_signature": signature,
        "sources": sources,
        "inspections": generation.get("pdf_inspections", []),
        "blocks": blocks,
        "issues": issues,
        "corrections": corrections,
        "removed_blocks": removed_blocks,
        "applied_operations": [c for c in result.get("corrections", []) if c['field'] == 'course_operation'],
        "regions": region_choices(result),
        **({"date_review": date_review_view(result["date_review"], generation)} if result.get("date_review") else {}),
        "revision": review_revision(result),
        "verification": verification_queue(blocks, signature, set(result.get("verified_items", []))),
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
    revision = review_revision(snapshot)
    blocks = list((snapshot.get("generation") or {}).get("blocks", []))
    target = next((block for block in blocks if str(block.get("block_id")) == block_id), None)
    if target is None:
        raise ValueError("未找到要修正的课程块。")
    original_value = target.get(field)
    normalized = _normalize_value(field, new_value)
    if _values_equivalent(original_value, normalized):
        # 空操作/重复提交不落库：日历拖拽固定提交周次/星期/节次三个字段，
        # 若不做比较会在导出“人工修正历史”里堆满 original==new 的噪声记录。
        return {"ok": True, "correction_id": "", **get_review_payload(store, job_id)}
    _apply_block_value(target, field, normalized)
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
        lock_review_revision(connection, job_id, revision)
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


def confirm_issue(store: StateStore, job_id: str, issue_id: str, *,
                  reason: str = "已对照原文核对", operator_id: str = "本机用户") -> dict[str, Any]:
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
    revision = review_revision(snapshot)
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
        lock_review_revision(connection, job_id, revision)
        store.confirm_issue(job_id, issue_id, connection=connection)
        from app.services.review_evidence import confirmation_receipt
        receipt = confirmation_receipt(snapshot, target)
        if receipt:
            receipt.update(reason=reason.strip(), operator_id=operator_id.strip())
            store.add_audit_event(job_id, "review.evidence", receipt, connection=connection)
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
    issue_count = len(workflow.process_result.issues)
    for source in workflow.generation_result.model_sources:
        if not source.content_hash:
            continue
        operation_chain_changed = False
        for correction in store.corrections_for_source(source.content_hash, signature):
            if correction["stale"]:
                continue
            from app.services.review_operations import OPERATION_FIELD, replay_operation
            if correction["field"] == OPERATION_FIELD:
                if operation_chain_changed:
                    continue
                before_issue_count = len(workflow.process_result.issues)
                if replay_operation(workflow, correction):
                    workflow.process_result.corrections.append(CorrectionRecord(**{
                        k: correction[k] for k in CorrectionRecord.__dataclass_fields__
                    }))
                    applied += 1
                elif len(workflow.process_result.issues) != before_issue_count:
                    operation_chain_changed = True
                continue
            target = next(
                (item for item in blocks if str(item.get("block_id")) == correction["block_id"]),
                None,
            )
            if target is None or correction["field"] not in EDITABLE_BLOCK_FIELDS:
                continue
            _apply_block_value(target, correction["field"], correction["new_value"])
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
    if applied or len(workflow.process_result.issues) != issue_count:
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
    text = str(value).strip()
    if field == "member_key" and (len(text.split("｜")) not in {3, 4} or not all(text.split("｜"))):
        raise ValueError("成员标识应包含姓名、部门和角色。")
    return text


def _values_equivalent(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, default=str
    )


def _apply_block_value(block: dict[str, Any], field: str, value: Any) -> None:
    block[field] = value
    if field == "periods":
        block["period"] = value[0] if value else None
    elif field == "member_key":
        parts = value.split("｜")
        block["name"], block["department"], block["role"] = parts[:3]
        block["display_suffix"] = parts[3] if len(parts) == 4 else ""
    elif field in {"name", "department", "role"}:
        current_key = str(block.get("member_key") or "").split("｜")
        suffix = str(block.get("display_suffix") or (current_key[3] if len(current_key) == 4 else ""))
        identity = MemberIdentity(
            name=str(block.get("name") or ""),
            department=str(block.get("department") or ""),
            role=str(block.get("role") or ""),
            display_suffix=suffix or None,
        )
        # 与 CourseBlock.__post_init__ 保持一致：身份不完整时用裸姓名，
        # 不生成 “姓名｜｜” 这类带空段的复合键。
        block["member_key"] = str(block.get("name") or "") if identity.confirmation_required else identity.member_key


def _refresh_derived(workflow: Any, schedule_core: Any) -> None:
    generation = workflow.generation_result
    generation.students = sorted({
        str(block.get("member_key") or block.get("name"))
        for block in generation.blocks + generation.removed_blocks
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
    from core.timetable_coverage import covered_weeks
    generation.weeks = covered_weeks(generation.pdf_inspections, generation.calendar_df, generation.weeks)
    date_map = build_date_map(generation.calendar_df)
    time_range = getattr(schedule_core, "period_time_range", None)
    for block in generation.blocks:
        try:
            week = int(block["week"])
            weekday = str(block["weekday"])
        except (KeyError, TypeError, ValueError):
            continue
        mapped_date = date_map.get((week, weekday))
        # 日历之外的周次（例如末行残留的第 19–21 周）不能被清空日期，
        # 否则任意一次人工修正都会让明细与快照丢失日期。
        if mapped_date:
            block["date"] = mapped_date
        # 修正 periods 后 time 必须跟着重算，否则导出会出现“节次 5 / 时间 08:10–08:55”。
        if callable(time_range):
            try:
                block["time"] = time_range(int(block.get("period")))
            except (TypeError, ValueError):
                pass
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
    from app.services.review_operations import restore_empty_source_membership
    restore_empty_source_membership(generation)
    generation.occupancy = schedule_core.build_occupancy(generation.blocks)
    generation.blocks_df = schedule_core.blocks_to_dataframe(generation.blocks)
    timetable = schedule_core.default_timetable()
    timetable, _ = schedule_core.validate_timetable(timetable)
    periods = [int(period) for period in timetable["period"]]
    generation.all_slot_df = align_slot_dates(
        schedule_core.build_slot_table(
            generation.occupancy,
            generation.students,
            generation.weeks,
            ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
            periods,
        ),
        generation.calendar_df,
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
    # 修正/重放后重新计算学期范围问题，与解析路径共用同一判定。
    calendar_weeks: set[int] = set()
    calendar_dates: list[str] = []
    frame = generation.calendar_df
    if frame is not None and not frame.empty:
        if "week" in frame:
            for value in frame["week"]:
                try:
                    calendar_weeks.add(int(value))
                except (TypeError, ValueError):
                    continue
        if "date" in frame:
            calendar_dates = [str(value) for value in frame["date"] if str(value).strip()]
    for code, message in schedule_range_violations(
        blocks=generation.blocks,
        calendar_weeks=calendar_weeks,
        earliest_calendar_date=min(calendar_dates) if calendar_dates else None,
    ):
        retained_issues.append(
            ParseIssue(
                code=code,
                message=message,
                severity="error",
                suggestion=SCHEDULE_RANGE_SUGGESTION,
                blocks_export=True,
            )
        )
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
    if workflow.date_review is not None:
        from app.services.date_review import apply_date_scope
        apply_date_scope(workflow, schedule_core, confirmed_issue_ids=confirmed_ids)


def snapshot_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in snapshot.get("issues", [])]

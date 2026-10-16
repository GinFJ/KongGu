"""Regression checks for corrections and source fidelity in desktop review."""

import json
from pathlib import Path

from app.services.desktop_workflow import load_workflow_result
from app.services.review_service import apply_correction, get_review_payload
from app.services.state_store import StateStore
from core import schedule_core


def _saved_job(tmp_path: Path, *, same_names: bool = False):
    store = StateStore(tmp_path / "state.sqlite3")
    member_key = "成员甲｜办公室｜干事"
    file_names = ["成员甲-中方课表.pdf", "成员甲-英方课表.pdf"]
    if same_names:
        file_names[1] = file_names[0]
    paths = [str(tmp_path / "first" / file_names[0]), str(tmp_path / "second" / file_names[1])]
    hashes = ["hash-cn", "hash-en"]
    blocks = []
    for index, (kind, period) in enumerate((("中方", 1), ("英方", 2))):
        block = {
            "block_id": f"block-{index}",
            "name": "成员甲",
            "member_key": member_key,
            "department": "办公室",
            "role": "干事",
            "source_type": kind,
            "kind": kind,
            "source_file": file_names[index],
            "source_hash": hashes[index],
            "week": 1,
            "weekday": "周一",
            "period": period,
            "periods": [period],
            "course": f"示例课程{index}",
        }
        blocks.append(block)
    snapshot = {
        "version": 1,
        "generation": {
            "sources": [
                {"file_name": name, "kind": kind, "source_path": "", "content_hash": source_hash}
                for name, kind, source_hash in zip(file_names, ("中方", "英方"), hashes)
            ],
            "blocks": blocks,
            "calendar_rows": [{"date": "2026-09-07", "week": 1, "weekday": "周一"}],
            "occupancy": [
                {"week": 1, "weekday": "周一", "period": period, "members": [member_key]}
                for period in (1, 2)
            ],
            "students": [member_key],
            "weeks": [1],
            "errors": [],
            "elapsed_seconds": 0,
            "pdf_inspections": [],
        },
        "quality_state": "accepted",
        "issues": [],
        "corrections": [],
        "parser_signature": "test-signature",
        "warnings": [],
        "errors": [],
    }
    job_id = store.create_job({}, "test-signature", paths)
    for path, source_hash in zip(paths, hashes):
        store.update_file(job_id, path, source_hash=source_hash)
    store.save_job_result(
        job_id,
        result_json=json.dumps(snapshot, ensure_ascii=False),
        error="",
        quality_state="accepted",
        current=2,
        total=2,
        issues=[],
    )
    return store, job_id, paths, member_key


def test_corrections_recompute_periods_weeks_members_and_quality(tmp_path: Path):
    store, job_id, _, member_key = _saved_job(tmp_path)

    for field, value in (("periods", [1, 3]), ("week", 3)):
        apply_correction(
            store=store,
            schedule_core=schedule_core,
            job_id=job_id,
            block_id="block-0",
            field=field,
            new_value=value,
            reason="原文核对",
            operator_id="测试者",
        )
    workflow = load_workflow_result(job_id, store, schedule_core)
    assert workflow.generation_result.weeks == [1, 3]
    assert workflow.generation_result.occupancy[(3, "周一", 1)] == {member_key}
    assert workflow.generation_result.occupancy[(3, "周一", 3)] == {member_key}
    assert any(slot.week == 3 and slot.period == 3 for slot in workflow.process_result.slots)

    apply_correction(
        store=store,
        schedule_core=schedule_core,
        job_id=job_id,
        block_id="block-0",
        field="member_key",
        new_value="成员乙｜办公室｜干事",
        reason="身份核对",
        operator_id="测试者",
    )
    workflow = load_workflow_result(job_id, store, schedule_core)
    assert set(workflow.generation_result.students) == {
        member_key, "成员乙｜办公室｜干事"
    }
    assert workflow.process_result.quality_state == "blocked"
    assert any(issue.code == "MEMBER_SCHEDULE_INCOMPLETE" for issue in workflow.process_result.issues)
    assert any(issue["code"] == "MEMBER_SCHEDULE_INCOMPLETE" for issue in store.get_job(job_id)["issues"])


def test_review_paths_match_content_hash_for_same_named_files(tmp_path: Path):
    store, job_id, paths, _ = _saved_job(tmp_path, same_names=True)

    sources = get_review_payload(store, job_id)["sources"]
    assert [source["source_path"] for source in sources] == paths

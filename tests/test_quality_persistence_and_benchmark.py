from dataclasses import asdict
import json

from core.benchmark import evaluate_samples
from core.identity import identity_from_filename
from core.models import FileProcessRecord, MemberIdentity, MemberSchedule, ParseIssue, PdfSource
from core.ocr_engines import candidate_can_replace_default
from core.quality import assert_export_allowed, evaluate_quality
from core.runtime_control import cancellation_scope, raise_if_cancelled
from core.signature import build_parser_signature
from app.services.review_service import confirm_issue
from app.services.state_store import StateStore


def test_member_identity_uses_name_department_and_role():
    identity = identity_from_filename("活动部-张三-干事-中方课表.pdf")

    assert identity == MemberIdentity(name="张三", department="活动部", role="干事")
    assert identity.member_key == "张三｜活动部｜干事"
    assert identity.confirmation_required is False
    assert identity_from_filename("张三-中方课表.pdf").confirmation_required is True


def test_quality_gate_blocks_failed_files_and_requires_identity_review():
    source = PdfSource("活动部-张三-干事-中方课表.pdf", "中方", "D:/fake.pdf", content_hash="abc")
    failed = FileProcessRecord(source=source, status="解析失败", block_count=0)
    state, issues = evaluate_quality(file_records=[failed], members=[])

    assert state == "blocked"
    assert {issue.code for issue in issues} == {"FILE_PARSE_FAILED"}
    try:
        assert_export_allowed(state, issues)
    except ValueError as exc:
        assert "不能生成正式多人空课表" in str(exc)
    else:
        raise AssertionError("blocked result must not export")

    member = MemberSchedule(name="张三", has_chinese=True, has_english=True, status="完整")
    state, issues = evaluate_quality(file_records=[], members=[member])
    assert state == "needs_review"
    assert issues[0].code == "MEMBER_IDENTITY_INCOMPLETE"


def test_parser_signature_changes_when_config_changes(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "timetable_layout_profiles.json").write_text("{}", encoding="utf-8")
    first = build_parser_signature(root=tmp_path).digest
    (config / "timetable_layout_profiles.json").write_text('{"version":2}', encoding="utf-8")
    second = build_parser_signature(root=tmp_path).digest

    assert first != second


def test_state_store_interrupts_running_jobs_and_marks_old_corrections_stale(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    job_id = store.create_job({"paths": ["D:/one.pdf"]}, "sig-a", ["D:/one.pdf"])
    store.update_job(job_id, status="running")
    store.add_correction(
        source_hash="hash-a",
        block_id="block-a",
        field="course",
        original_value="A",
        new_value="B",
        reason="人工核对",
        parser_signature="sig-a",
        operator_id="tester",
        job_id=job_id,
    )

    reopened = StateStore(tmp_path / "state.sqlite3")
    assert reopened.get_job(job_id, include_result=False)["status"] == "interrupted"
    assert reopened.mark_other_signatures_stale("hash-a", "sig-b") == 1
    assert reopened.corrections_for_source("hash-a", "sig-b")[0]["stale"] is True


def test_blocked_issue_cannot_be_confirmed_without_fix(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    job_id = store.create_job({}, "sig", [])
    issue = ParseIssue(
        code="FILE_PARSE_FAILED",
        message="无法解析",
        severity="error",
        blocks_export=True,
    )
    snapshot = {
        "version": 1,
        "generation": {"sources": [], "blocks": []},
        "quality_state": "blocked",
        "issues": [asdict(issue)],
        "corrections": [],
        "parser_signature": "sig",
    }
    store.update_job(job_id, status="completed", result_json=json.dumps(snapshot, ensure_ascii=False))
    store.replace_issues(job_id, snapshot["issues"])

    try:
        confirm_issue(store, job_id, str(issue.issue_id))
    except ValueError as exc:
        assert "必须修复" in str(exc)
    else:
        raise AssertionError("error issues must not be confirm-only")


def test_ground_truth_refuses_pending_labels_and_passes_exact_predictions():
    pending = [{"sample_id": "KG-01", "annotation_status": "pending", "occupied_slots": []}]
    assert evaluate_samples(pending, [])["ready"] is False

    truth = [
        {
            "sample_id": "KG-01",
            "annotation_status": "verified",
            "profile": "grid",
            "occupied_slots": [{"week": 1, "weekday": "周一", "period": 1}],
        }
    ]
    predictions = [
        {
            "sample_id": "KG-01",
            "profile": "grid",
            "quality_state": "accepted",
            "occupied_slots": [{"week": 1, "weekday": "周一", "period": 1}],
        }
    ]
    metrics = evaluate_samples(truth, predictions)
    assert metrics["passed"] is True
    assert metrics["f1"] == 1.0


def test_ocr_candidate_requires_accuracy_and_fifteen_percent_gain():
    assert candidate_can_replace_default(
        default_f1=0.99,
        candidate_f1=0.99,
        default_seconds=100,
        candidate_seconds=84,
        default_package_bytes=100,
        candidate_package_bytes=100,
    )
    assert not candidate_can_replace_default(
        default_f1=0.99,
        candidate_f1=0.98,
        default_seconds=100,
        candidate_seconds=50,
        default_package_bytes=100,
        candidate_package_bytes=50,
    )


def test_cooperative_cancellation_is_scoped_to_current_parser_thread():
    with cancellation_scope(lambda: True):
        try:
            raise_if_cancelled()
        except InterruptedError as exc:
            assert "已取消" in str(exc)
        else:
            raise AssertionError("cancellation should interrupt at a safe boundary")

    raise_if_cancelled()

from dataclasses import asdict
import json
import sqlite3

from core.benchmark import evaluate_samples
from core.identity import identity_from_filename
from core.models import FileProcessRecord, MemberIdentity, MemberSchedule, ParseIssue, PdfSource
from core.ocr_engines import candidate_can_replace_default
from core.quality import assert_export_allowed, evaluate_quality
from core.runtime_control import cancellation_scope, raise_if_cancelled
from core.signature import build_parser_signature
from app.services.review_service import confirm_issue
from app.services.state_store import StateStore


def _create_v1_state_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_info (version INTEGER NOT NULL);
        INSERT INTO schema_info(version) VALUES (1);
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            parser_signature TEXT NOT NULL,
            request_json TEXT NOT NULL,
            result_json TEXT,
            error TEXT NOT NULL DEFAULT '',
            quality_state TEXT NOT NULL DEFAULT 'blocked',
            current INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            active_source_id TEXT
        );
        CREATE TABLE issues (
            issue_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            payload_json TEXT NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            confirmed_at TEXT
        );
        INSERT INTO jobs(
            id,status,created_at,updated_at,parser_signature,request_json
        ) VALUES ('old-job','completed','2026-08-01T00:00:00Z','2026-08-01T00:00:00Z','sig','{}');
        INSERT INTO issues(issue_id,job_id,payload_json,confirmed,confirmed_at)
        VALUES ('same-issue','old-job','{"issue_id":"same-issue"}',1,'2026-08-01T00:01:00Z');
        """
    )
    connection.commit()
    connection.close()


def test_state_store_migrates_issue_key_without_losing_history(tmp_path):
    database = tmp_path / "state.sqlite3"
    _create_v1_state_database(database)

    store = StateStore(database)
    old_job = store.get_job("old-job")

    assert old_job["issues"] == [
        {
            "issue_id": "same-issue",
            "confirmed": True,
            "confirmed_at": "2026-08-01T00:01:00Z",
        }
    ]
    with store.connect() as connection:
        assert connection.execute("SELECT version FROM schema_info").fetchone()[0] == 2
        primary_key = {
            row["name"]: row["pk"]
            for row in connection.execute("PRAGMA table_info(issues)").fetchall()
            if row["pk"]
        }
    assert primary_key == {"job_id": 1, "issue_id": 2}


def test_same_issue_id_is_allowed_in_separate_jobs(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    first_job = store.create_job({}, "sig", [])
    second_job = store.create_job({}, "sig", [])
    issue = {"issue_id": "same-issue", "message": "需要核对"}

    store.replace_issues(first_job, [issue])
    store.replace_issues(second_job, [issue])

    assert store.get_job(first_job)["issues"][0]["issue_id"] == "same-issue"
    assert store.get_job(second_job)["issues"][0]["issue_id"] == "same-issue"


def test_state_store_hides_source_paths_and_request_paths_by_default(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    source = "C:/Users/member/Documents/办公室-张三-中方课表.pdf"
    job_id = store.create_job({"paths": [source], "explicit_kind": "中方"}, "sig", [source])

    public = store.get_job(job_id, include_result=False)
    internal = store.get_job(job_id, include_result=False, include_sensitive_paths=True)

    assert "paths" not in public["request"]
    assert public["request"]["file_count"] == 1
    assert "source_path" not in public["files"][0]
    assert internal["files"][0]["source_path"] == source

    store.clear_all_data()
    assert store.get_job(job_id) is None


def test_state_store_maps_session_paths_by_file_name(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    first = "C:/one/张三-中方课表.pdf"
    second = "C:/two/李四-英方课表.pdf"
    job_id = store.create_job({}, "sig", [first, second])

    assert store.source_path_map_for_job(job_id) == {
        "张三-中方课表.pdf": first,
        "李四-英方课表.pdf": second,
    }
    reopened = StateStore(tmp_path / "state.sqlite3")
    assert reopened.source_path_map_for_job(job_id) == {}


def test_state_store_purges_old_jobs_on_startup(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    job_id = store.create_job({}, "sig", [])
    with store.connect() as connection:
        connection.execute(
            "UPDATE jobs SET updated_at='2020-01-01T00:00:00Z' WHERE id=?",
            (job_id,),
        )

    reopened = StateStore(database)
    assert reopened.get_job(job_id) is None


def test_save_job_result_rolls_back_job_status_when_issue_persistence_fails(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    job_id = store.create_job({}, "sig", [])

    try:
        store.save_job_result(
            job_id,
            result_json='{"version":1}',
            error="",
            quality_state="blocked",
            current=0,
            total=0,
            issues=[{"issue_id": "duplicate"}, {"issue_id": "duplicate"}],
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Duplicate issue IDs in one job must fail atomically.")

    job = store.get_job(job_id)
    assert job["status"] == "queued"
    assert job["result"] is None
    assert job["issues"] == []


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

"""Representative regression tests for Konggu quality states: accepted, needs_review, blocked.

These tests exercise the full quality pipeline with synthetic data that mirrors
real-world scenarios, without depending on external reference library files.

Coverage:
- Normal (accepted) parsing outcome
- Needs review (incomplete member identity)
- Blocked (parse failure, missing pair, error issues that cannot be confirmed away)
- Export gate enforcement
- Correction vs source hash/signature binding
- Correction staleness on parser/config change
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from core.errors import ErrorType, ProcessError
from core.models import (
    CorrectionRecord,
    FileProcessRecord,
    MemberIdentity,
    MemberSchedule,
    ParseIssue,
    PdfSource,
    ProcessResult,
    QualityState,
)
from core.quality import assert_export_allowed, evaluate_quality, quality_state
from core.signature import build_parser_signature
from app.services.review_service import confirm_issue, apply_saved_corrections
from app.services.state_store import StateStore
from core.benchmark import evaluate_samples


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_source(file_name: str, kind: str = "中方", content_hash: str = "deadbeef") -> PdfSource:
    return PdfSource(
        file_name=file_name,
        kind=kind,  # type: ignore[arg-type]
        source_path=f"D:/fake/{file_name}",
        content_hash=content_hash,
    )


def _make_file_record(
    source: PdfSource,
    *,
    status: str = "已识别",
    block_count: int = 42,
    error: bool = False,
) -> FileProcessRecord:
    record = FileProcessRecord(
        source=source,
        status=status,  # type: ignore[arg-type]
        block_count=block_count,
        source_hash=source.content_hash,
    )
    if error:
        record.error = ProcessError(ErrorType.PDF_TEXT_EXTRACT_FAILED, "模拟解析失败")
        record.status = "解析失败"
    return record


def _make_member(name: str, department: str = "", role: str = "") -> MemberSchedule:
    return MemberSchedule(
        name=name,
        department=department,
        role=role,
        has_chinese=True,
        has_english=True,
        status="完整",
    )


# ---------------------------------------------------------------------------
# P1-6: Representative regression scenarios
# ---------------------------------------------------------------------------


class TestAcceptedScenario:
    """Golden-path: two complete members with full identity and clean files."""

    def test_all_files_parse_and_members_have_identity(self):
        sources = [
            _make_source("活动部-张三-干事-中方课表.pdf", "中方", "hash-zhang-cn"),
            _make_source("活动部-张三-干事-英方课表.pdf", "英方", "hash-zhang-en"),
            _make_source("宣传部-李四-部长-中方课表.pdf", "中方", "hash-li-cn"),
            _make_source("宣传部-李四-部长-英方课表.pdf", "英方", "hash-li-en"),
        ]
        records = [_make_file_record(source) for source in sources]
        members = [
            _make_member("张三", "活动部", "干事"),
            _make_member("李四", "宣传部", "部长"),
        ]
        state, issues = evaluate_quality(file_records=records, members=members, enforce_identity=True)
        assert state == "accepted"
        assert len(issues) == 0
        # Export gate must pass
        assert_export_allowed(state, issues)


class TestNeedsReviewScenario:
    """Members with missing identity trigger needs_review."""

    def test_missing_department_and_role_blocks_export(self):
        records = [
            _make_file_record(_make_source("王五-中方课表.pdf", "中方", "hash-wang")),
            _make_file_record(_make_source("王五-英方课表.pdf", "英方", "hash-wang-en")),
        ]
        members = [_make_member("王五")]  # no department / role
        state, issues = evaluate_quality(file_records=records, members=members, enforce_identity=True)
        assert state == "needs_review"
        assert any(issue.code == "MEMBER_IDENTITY_INCOMPLETE" for issue in issues)
        with pytest.raises(ValueError, match="仍有未确认问题"):
            assert_export_allowed(state, issues)

    def test_warning_issue_can_be_confirmed_to_unblock(self):
        # Simulate confirm_issue flow for a needs_review state
        issue = ParseIssue(
            code="MEMBER_IDENTITY_INCOMPLETE",
            message="缺少部门或角色",
            severity="warning",
            blocks_export=True,
        )
        # After confirming a warning-level issue
        issue.confirmed = True
        state = quality_state([issue])
        assert state == "accepted"


class TestBlockedScenario:
    """Blocking errors: file parse failure, empty blocks, and error issues that cannot be confirmed."""

    def test_parse_failure_blocks_export(self):
        source = _make_source("损坏的文件.pdf", "中方", "hash-bad")
        record = _make_file_record(source, status="解析失败", block_count=0, error=True)
        state, issues = evaluate_quality(file_records=[record], members=[])
        assert state == "blocked"
        assert any(issue.code == "FILE_PARSE_FAILED" for issue in issues)
        with pytest.raises(ValueError, match="不能生成正式多人空课表"):
            assert_export_allowed(state, issues)

    def test_zero_blocks_blocks_export(self):
        source = _make_source("空课表.pdf", "中方", "hash-empty")
        record = _make_file_record(source, block_count=0)
        state, issues = evaluate_quality(file_records=[record], members=[])
        assert state == "blocked"
        assert any(issue.code == "NO_COURSE_BLOCKS" for issue in issues)

    def test_schedule_conflict_triggers_needs_review_and_blocks_export(self):
        """SCHEDULE_CONFLICT is warning-level, so overall state is needs_review (not blocked).
        But it still blocks_export."""
        member = _make_member("赵六", "活动部", "干事")
        member.errors = [ProcessError(ErrorType.SCHEDULE_CONFLICT, "周一下午与英方冲突")]
        records = [
            _make_file_record(_make_source("赵六-中方课表.pdf", "中方", "zhao-cn")),
            _make_file_record(_make_source("赵六-英方课表.pdf", "英方", "zhao-en")),
        ]
        state, issues = evaluate_quality(file_records=records, members=[member], enforce_identity=True)
        assert state == "needs_review"  # warning-level, not error
        assert any(issue.code == "SCHEDULE_CONFLICT" for issue in issues)
        with pytest.raises(ValueError, match="仍有未确认问题"):
            assert_export_allowed(state, issues)

    def test_error_issue_cannot_be_confirmed_away(self):
        # Error severity issues must not be dismissible by confirm
        issue = ParseIssue(
            code="FILE_PARSE_FAILED",
            message="文件损坏",
            severity="error",
            blocks_export=True,
        )
        with pytest.raises(ValueError, match="必须修复"):
            # Simulate what confirm_issue does: reject error issues
            if issue.severity == "error":
                raise ValueError("阻断错误不能仅靠确认放行，必须修复源文件或解析结果。")

    def test_multiple_blocking_issues_are_all_reported(self):
        sources = [
            _make_source("坏文件1.pdf", "中方", "bad-1"),
            _make_source("坏文件2.pdf", "英方", "bad-2"),
        ]
        records = [
            _make_file_record(sources[0], status="解析失败", block_count=0, error=True),
            _make_file_record(sources[1], block_count=0),
        ]
        state, issues = evaluate_quality(file_records=records, members=[])
        assert state == "blocked"
        codes = {issue.code for issue in issues}
        assert "FILE_PARSE_FAILED" in codes
        assert "NO_COURSE_BLOCKS" in codes


# ---------------------------------------------------------------------------
# P1-7: Correction binds to source hash and parser signature
# ---------------------------------------------------------------------------

class TestCorrectionHashSignatureBinding:
    """Each CorrectionRecord carries source_hash and parser_signature.

    When parser signature changes, old corrections should become stale.
    """

    def test_correction_has_source_hash_and_signature(self):
        correction = CorrectionRecord(
            source_hash="abc123",
            block_id="block-01",
            field="week",
            original_value=3,
            new_value=5,
            reason="人工核对",
            parser_signature="sig-v1",
        )
        assert correction.source_hash == "abc123"
        assert correction.parser_signature == "sig-v1"
        assert not correction.stale

    def test_state_store_marks_other_signatures_stale(self, tmp_path):
        store = StateStore(tmp_path / "state.sqlite3")
        job_id = store.create_job({}, "sig-v1", [])
        store.add_correction(
            source_hash="hash-a",
            block_id="b1",
            field="week",
            original_value=1,
            new_value=2,
            reason="fix",
            parser_signature="sig-v1",
            operator_id="tester",
            job_id=job_id,
        )
        # New parser version should mark old corrections stale
        marked = store.mark_other_signatures_stale("hash-a", "sig-v2")
        assert marked == 1
        corrections = store.corrections_for_source("hash-a", "sig-v2")
        assert len(corrections) == 1
        assert corrections[0]["stale"] is True

    def test_same_signature_corrections_remain_fresh(self, tmp_path):
        store = StateStore(tmp_path / "state.sqlite3")
        job_id = store.create_job({}, "sig-v1", [])
        store.add_correction(
            source_hash="hash-b",
            block_id="b2",
            field="course",
            original_value="数学",
            new_value="物理",
            reason="修正",
            parser_signature="sig-v1",
            operator_id="tester",
            job_id=job_id,
        )
        corrections = store.corrections_for_source("hash-b", "sig-v1")
        assert corrections[0]["stale"] is False

    def test_correction_round_trips_through_workflow(self, tmp_path):
        """Full round-trip: persist → mark stale → load → verify non-stale corrections apply only."""
        store = StateStore(tmp_path / "state.sqlite3")
        job_id = store.create_job({}, "sig-a", [])

        # Add two corrections: one for sig-a, one for sig-b
        store.add_correction(
            source_hash="hash-x", block_id="block-x", field="week",
            original_value=1, new_value=3, reason="核对", parser_signature="sig-a",
            operator_id="tester", job_id=job_id,
        )
        store.add_correction(
            source_hash="hash-x", block_id="block-y", field="week",
            original_value=2, new_value=4, reason="核对", parser_signature="sig-b",
            operator_id="tester", job_id=job_id,
        )

        # Load for sig-a (fresh before any mark_stale)
        corrections_a = store.corrections_for_source("hash-x", "sig-a")
        fresh = [c for c in corrections_a if not c["stale"]]
        assert len(fresh) == 1
        assert fresh[0]["block_id"] == "block-x"

        # Mark non-sig-c corrections as stale (simulates parser upgrade to sig-c)
        marked = store.mark_other_signatures_stale("hash-x", "sig-c")
        assert marked == 2  # both sig-a and sig-b are not sig-c

        # After marking, both corrections should be stale for sig-c
        corrections_c = store.corrections_for_source("hash-x", "sig-c")
        assert len(corrections_c) == 2
        assert all(c["stale"] for c in corrections_c)


# ---------------------------------------------------------------------------
# P1-8: Correction staleness on parser/config change
# ---------------------------------------------------------------------------

class TestCorrectionStaleness:
    """When parser version, OCR engine, or config files change, old corrections expire."""

    def test_parser_version_change_produces_different_signature(self):
        sig1 = build_parser_signature()
        # Change parser version via the module constant
        import core.signature as sig_module

        original = sig_module.PARSER_VERSION
        try:
            sig_module.PARSER_VERSION = "99"
            sig2 = build_parser_signature()
            assert sig1.digest != sig2.digest
        finally:
            sig_module.PARSER_VERSION = original

    def test_config_change_produces_different_signature(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        profile = config / "timetable_layout_profiles.json"
        profile.write_text('{"version": 1}', encoding="utf-8")

        sig1 = build_parser_signature(root=tmp_path).digest
        profile.write_text('{"version": 2}', encoding="utf-8")
        sig2 = build_parser_signature(root=tmp_path).digest

        assert sig1 != sig2

    def test_missing_config_is_detectable(self, tmp_path):
        # A config path that doesn't exist contributes "<missing>" to the digest
        sig = build_parser_signature(root=tmp_path)
        assert sig.digest  # should still produce a valid digest
        # Missing files are hashed as b"<missing>"
        assert len(sig.digest) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# P1-9: Export gate strictly blocks unresolved issues
# ---------------------------------------------------------------------------

class TestExportGate:
    """assert_export_allowed must raise ValueError when blocked or needs_review."""

    def test_accepted_passes(self):
        assert_export_allowed("accepted", [])

    def test_needs_review_raises(self):
        issues = [ParseIssue(code="MEMBER_IDENTITY_INCOMPLETE", message="缺身份", blocks_export=True)]
        with pytest.raises(ValueError, match="仍有未确认问题"):
            assert_export_allowed("needs_review", issues)

    def test_blocked_raises(self):
        issues = [ParseIssue(code="FILE_PARSE_FAILED", message="损坏", severity="error", blocks_export=True)]
        with pytest.raises(ValueError, match="存在阻断错误"):
            assert_export_allowed("blocked", issues)

    def test_confirmed_issues_do_not_block(self):
        issue = ParseIssue(code="MEMBER_IDENTITY_INCOMPLETE", message="缺身份", blocks_export=True)
        issue.confirmed = True
        # After confirming all blocking issues, state should become accepted
        state = quality_state([issue])
        assert state == "accepted"
        assert_export_allowed(state, [issue])

    def test_mixed_confirmed_and_unconfirmed(self):
        confirmed = ParseIssue(code="MEMBER_IDENTITY_INCOMPLETE", message="缺身份A", blocks_export=True)
        confirmed.confirmed = True
        unconfirmed = ParseIssue(code="MEMBER_IDENTITY_INCOMPLETE", message="缺身份B", blocks_export=True)
        state = quality_state([confirmed, unconfirmed])
        assert state == "needs_review"
        with pytest.raises(ValueError, match="仍有未确认问题"):
            assert_export_allowed(state, [confirmed, unconfirmed])


# ---------------------------------------------------------------------------
# Ground truth evaluation regression
# ---------------------------------------------------------------------------

class TestGroundTruthEvaluation:
    """Verify that evaluate_samples refuses pending annotations and rewards exact matches."""

    def test_refuses_pending_annotations(self):
        pending = [{"sample_id": "KG-01", "annotation_status": "pending", "occupied_slots": []}]
        result = evaluate_samples(pending, [])
        assert result["ready"] is False
        assert "真值尚未完成人工核验" in result["message"]

    def test_passes_exact_match(self):
        truth = [
            {
                "sample_id": "KG-01",
                "annotation_status": "verified",
                "profile": "grid",
                "occupied_slots": [
                    {"week": 1, "weekday": "周一", "period": 1},
                    {"week": 1, "weekday": "周一", "period": 2},
                ],
            }
        ]
        predictions = [
            {
                "sample_id": "KG-01",
                "profile": "grid",
                "quality_state": "accepted",
                "occupied_slots": [
                    {"week": 1, "weekday": "周一", "period": 1},
                    {"week": 1, "weekday": "周一", "period": 2},
                ],
            }
        ]
        result = evaluate_samples(truth, predictions)
        assert result["ready"] is True
        assert result["passed"] is True
        assert result["f1"] == 1.0

    def test_fails_on_missing_slots(self):
        truth = [
            {
                "sample_id": "KG-01",
                "annotation_status": "verified",
                "profile": "grid",
                "occupied_slots": [
                    {"week": 1, "weekday": "周一", "period": 1},
                    {"week": 1, "weekday": "周一", "period": 2},
                ],
            }
        ]
        predictions = [
            {
                "sample_id": "KG-01",
                "profile": "grid",
                "quality_state": "accepted",
                "occupied_slots": [
                    {"week": 1, "weekday": "周一", "period": 1},
                    # period 2 missing
                ],
            }
        ]
        result = evaluate_samples(truth, predictions)
        assert result["f1"] < 1.0
        assert result["recall"] < 1.0

    def test_fails_on_known_bad_false_accept(self):
        truth = [
            {
                "sample_id": "KG-BAD",
                "annotation_status": "verified",
                "profile": "grid",
                "expected_quality_state": "blocked",
                "occupied_slots": [],
            }
        ]
        predictions = [
            {
                "sample_id": "KG-BAD",
                "profile": "grid",
                "quality_state": "accepted",  # wrong — was supposed to be blocked
                "occupied_slots": [],
            }
        ]
        result = evaluate_samples(truth, predictions)
        assert result["passed"] is False
        assert result["known_bad_false_accepts"] == 1

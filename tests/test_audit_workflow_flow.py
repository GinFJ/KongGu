"""Workflow-runtime regressions: progress mapping, cancel races, NDJSON, display names.

All cases use synthetic data and a fake schedule core so they never depend on
real timetable samples.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
import pandas as pd
import pytest

import app.sidecar as sidecar
import app.services.job_service as job_service
from app.services.availability_preview_service import build_availability_preview
from app.services.desktop_serialization import serialize_process_result
from app.services.job_service import JobCoordinator
from app.services.result_view_service import build_gui_process_result
from app.services.state_store import StateStore
from core.models import MemberSchedule


def _pdf_bytes(text: str = "Konggu timetable Monday Week 1") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def _member_name(file_name: str) -> str:
    return file_name.split("-")[0]


class RecordingCore:
    """Minimal schedule core that reports 1-based per-file parse progress."""

    def __init__(self) -> None:
        self.export_kwargs: dict | None = None

    def infer_pdf_kind(self, file_name, path=""):
        if "英方" in file_name:
            return "英方"
        if "中方" in file_name:
            return "中方"
        return ""

    def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None, progress=None):
        blocks = []
        for index, source in enumerate(sources):
            file_name = str(source["file_name"])
            name = _member_name(file_name)
            period = index + 1
            blocks.append(
                {
                    "name": name,
                    "member_key": f"{name}｜活动部｜干事",
                    "department": "活动部",
                    "role": "干事",
                    "source_type": source["kind"],
                    "kind": source["kind"],
                    "week": 1,
                    "date": "2026-09-07",
                    "weekday": "周一",
                    "period": period,
                    "periods": [period],
                    "course": "示例课程",
                    "source_file": file_name,
                }
            )
            if progress:
                progress(index + 1, len(sources), f"{file_name} 已完成 {index + 1}/{len(sources)} 份课表")
        calendar = pd.DataFrame([{"date": "2026-09-07", "week": 1, "weekday": "周一"}])
        return blocks, calendar, [], pd.DataFrame(blocks)

    def synthesize_calendar_from_blocks(self, blocks):
        return pd.DataFrame([{"date": "2026-09-07", "week": 1, "weekday": "周一"}])

    def build_occupancy(self, blocks):
        occupancy: dict[tuple[int, str, int], set[str]] = {}
        for block in blocks:
            key = (int(block["week"]), str(block["weekday"]), int(block["period"]))
            occupancy.setdefault(key, set()).add(str(block.get("member_key") or block["name"]))
        return occupancy

    def build_slot_table(self, occupancy, students, weeks, weekdays, periods):
        return pd.DataFrame([{"week": 1, "free_members": ""}])

    def blocks_to_dataframe(self, blocks):
        return pd.DataFrame(blocks)

    def default_timetable(self):
        return pd.DataFrame([{"period": 1, "start": "08:10", "end": "08:55"}])

    def validate_timetable(self, timetable):
        return timetable, []

    def build_empty_schedule_excel_bytes(self, **kwargs):
        self.export_kwargs = kwargs
        return b"xlsx"


def _job_view(store: StateStore, job_id: str) -> dict:
    return store.get_job(job_id, include_result=False, include_sensitive_paths=True)


def test_job_progress_events_follow_the_actual_file_row(tmp_path: Path):
    first = tmp_path / "张三-中方课表.pdf"
    first.write_bytes(_pdf_bytes())
    missing = tmp_path / "李四-英方课表.pdf"
    second = tmp_path / "王五-英方课表.pdf"
    second.write_bytes(_pdf_bytes("Konggu timetable Tuesday Week 2"))

    store = StateStore(tmp_path / "state.sqlite3")
    events: list[dict] = []
    coordinator = JobCoordinator(RecordingCore(), store, event_sink=events.append)

    started = coordinator.start(
        {"paths": [str(first), str(missing), str(second)]},
        background=False,
    )
    job = _job_view(store, started["job_id"])
    row_ids = {row["file_name"]: row["id"] for row in job["files"]}

    completed_events = [event for event in events if "份课表" in str(event.get("message") or "")]
    assert len(completed_events) == 2
    assert [event["source_id"] for event in completed_events] == [
        row_ids["张三-中方课表.pdf"],
        row_ids["王五-英方课表.pdf"],
    ]

    statuses = {row["file_name"]: row["status"] for row in _job_view(store, started["job_id"])["files"]}
    assert statuses["李四-英方课表.pdf"] == "failed"
    assert statuses["张三-中方课表.pdf"] == "completed"
    assert statuses["王五-英方课表.pdf"] == "completed"


def _completion_sink(store: StateStore, action):
    state = {"done": False}
    events: list[dict] = []

    def sink(event: dict) -> None:
        events.append(event)
        if state["done"]:
            return
        if event.get("stage") in {"review", "completed"} and not event.get("source_id"):
            state["done"] = True
            action(event, store)

    sink.events = events  # type: ignore[attr-defined]
    return sink


def test_cancel_arriving_at_completion_keeps_completed_job_status(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_pdf_bytes())
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator: JobCoordinator | None = None

    def cancel_late(event, _store):
        assert coordinator is not None
        coordinator.cancel(event["job_id"])

    sink = _completion_sink(store, cancel_late)
    coordinator = JobCoordinator(RecordingCore(), store, event_sink=sink)
    started = coordinator.start({"paths": [str(pdf)]}, background=False)

    job = store.get_job(started["job_id"])
    assert job["status"] == "completed"
    assert job["result"] is not None
    statuses = {row["file_name"]: row["status"] for row in job["files"]}
    assert statuses == {"张三-中方课表.pdf": "completed"}


def test_late_cancelling_write_after_completion_is_rolled_back(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_pdf_bytes())
    store = StateStore(tmp_path / "state.sqlite3")

    def late_cancelling_write(event, _store):
        # Simulate another thread whose cancel() read the running status just
        # before the job persisted its completed state, then wrote afterwards.
        _store.update_job(event["job_id"], status="cancelling", error="正在完成当前图片页后停止。")

    coordinator = JobCoordinator(RecordingCore(), store, event_sink=_completion_sink(store, late_cancelling_write))
    started = coordinator.start({"paths": [str(pdf)]}, background=False)

    assert store.get_job(started["job_id"])["status"] == "completed"


def test_start_clears_active_job_when_worker_thread_cannot_start(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_pdf_bytes())
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = JobCoordinator(RecordingCore(), store)

    class FailingThread:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("can't start new thread")

    with monkeypatch.context() as patch:
        patch.setattr(job_service.threading, "Thread", FailingThread)
        with pytest.raises(RuntimeError):
            coordinator.start({"paths": [str(pdf)]})

    assert coordinator._active_job_id is None
    with store.connect() as connection:
        rows = connection.execute("SELECT id, status FROM jobs ORDER BY created_at").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"

    retried = coordinator.start({"paths": [str(pdf)]}, background=False)
    assert store.get_job(retried["job_id"])["status"] == "completed"


def test_serve_decodes_utf8_ndjson_requests_when_windows_stdio_uses_ansi(monkeypatch):
    request = {
        "id": "请求-1",
        "command": "settings.calendar.save",
        "settings": {"semester_start_date": "2026-09-07", "teaching_weeks": 20},
        "note": "中文路径",
    }
    raw_stdin = io.BytesIO((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
    stdin = io.TextIOWrapper(raw_stdin, encoding="cp936")
    raw_stdout = io.BytesIO()
    stdout = io.TextIOWrapper(raw_stdout, encoding="cp936")
    monkeypatch.setattr(sidecar.sys, "stdin", stdin)
    monkeypatch.setattr(sidecar.sys, "stdout", stdout)
    monkeypatch.setattr(sidecar, "configure_offline_environment", lambda: object())
    monkeypatch.setattr(sidecar, "load_calendar_settings", lambda paths: {})
    monkeypatch.setattr(sidecar, "apply_calendar_settings", lambda settings: None)
    monkeypatch.setattr(sidecar, "load_schedule_core", lambda: object())
    monkeypatch.setattr(sidecar, "StateStore", type("FakeStore", (), {"from_resource_paths": staticmethod(lambda paths: object())}))
    monkeypatch.setattr(sidecar, "JobCoordinator", lambda *args, **kwargs: object())

    seen: list[dict] = []

    def fake_dispatch(payload, coordinator):
        seen.append(payload)
        return {"ok": True, "echo": payload.get("note")}

    monkeypatch.setattr(sidecar, "dispatch", fake_dispatch)

    assert sidecar.serve() == 0
    stdout.flush()

    assert seen and seen[0]["note"] == "中文路径"
    assert seen[0]["settings"]["semester_start_date"] == "2026-09-07"
    payload = json.loads(raw_stdout.getvalue().decode("utf-8"))
    assert payload["id"] == "请求-1"
    assert payload["data"]["echo"] == "中文路径"


def test_availability_preview_hides_composite_member_keys():
    students = ["张三｜活动部｜干事", "李四｜办公室｜干事"]
    result = build_availability_preview(
        occupancy={(1, "周一", 1): {students[0]}},
        students=students,
        weeks=[1],
        weekdays=["周一"],
        calendar_df=pd.DataFrame([{"date": "2026-09-07", "week": 1, "weekday": "周一"}]),
        timetable_df=pd.DataFrame([{"period": 1, "start": "08:10", "end": "08:55"}]),
    )

    row = result.free_df.iloc[0]
    assert row["空闲人员"] == "李四"
    assert row["有课人员"] == "张三"
    assert row["空闲人数"] == 1
    assert row["占用人数"] == 1
    assert result.slots[0].free_members == ["李四"]
    assert result.slots[0].busy_members == ["张三"]


def test_availability_preview_keeps_full_key_when_display_names_collide():
    students = ["张三｜活动部｜干事", "张三｜办公室｜干事"]
    result = build_availability_preview(
        occupancy={(1, "周一", 1): {students[0]}},
        students=students,
        weeks=[1],
        weekdays=["周一"],
        calendar_df=pd.DataFrame([{"date": "2026-09-07", "week": 1, "weekday": "周一"}]),
        timetable_df=pd.DataFrame([{"period": 1, "start": "08:10", "end": "08:55"}]),
    )

    row = result.free_df.iloc[0]
    assert row["有课人员"] == students[0]
    assert row["空闲人员"] == students[1]
    assert row["空闲人数"] == 1


def test_payload_summary_complete_count_matches_core_summary():
    member = MemberSchedule(name="张三", has_chinese=True, has_english=True, status="完整")
    process = build_gui_process_result(
        course_blocks=[],
        members=[member],
        file_records=[],
        slots=[],
        student_count=1,
        block_count=0,
        week_count=1,
        calendar_df=pd.DataFrame(),
    )

    payload = serialize_process_result(
        result=process,
        preview_df=pd.DataFrame(),
        warnings=[],
        errors=[],
        result_ref="result-ref",
    )

    assert payload["summary"]["member_count"] == process.summary.member_count
    assert payload["summary"]["complete_member_count"] == process.summary.complete_member_count


def test_event_sink_failure_does_not_mark_completed_job_failed(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_pdf_bytes())
    store = StateStore(tmp_path / "state.sqlite3")
    state = {"raised": False}

    def sink(event: dict) -> None:
        if state["raised"]:
            return
        if event.get("stage") in {"review", "completed"} and not event.get("source_id"):
            state["raised"] = True
            raise BrokenPipeError("event channel closed")

    coordinator = JobCoordinator(RecordingCore(), store, event_sink=sink)
    started = coordinator.start({"paths": [str(pdf)]}, background=False)

    job = store.get_job(started["job_id"])
    assert job["status"] == "completed"
    assert job["result"] is not None


class MixedProgressCore(RecordingCore):
    """Replays the parser core's mixed 0-based OCR / 1-based completion progress."""

    def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None, progress=None):
        for index, source in enumerate(sources):
            if progress:
                progress(
                    index,
                    len(sources),
                    f"{source['file_name']} 正在进行图片文字识别（第 1/1 页）",
                )
        return super().parse_actual_pdf_sources(sources, uploaded_calendar_df, progress)


def test_job_progress_normalizes_mixed_parser_progress_semantics(tmp_path: Path):
    first = tmp_path / "张三-中方课表.pdf"
    first.write_bytes(_pdf_bytes())
    second = tmp_path / "李四-英方课表.pdf"
    second.write_bytes(_pdf_bytes("Konggu timetable Tuesday Week 2"))

    store = StateStore(tmp_path / "state.sqlite3")
    events: list[dict] = []
    coordinator = JobCoordinator(MixedProgressCore(), store, event_sink=events.append)
    started = coordinator.start({"paths": [str(first), str(second)]}, background=False)
    job = _job_view(store, started["job_id"])
    row_ids = {row["file_name"]: row["id"] for row in job["files"]}

    ocr_events = [event for event in events if "正在进行图片文字识别" in str(event.get("message") or "")]
    assert [event["source_id"] for event in ocr_events] == [
        row_ids["张三-中方课表.pdf"],
        row_ids["李四-英方课表.pdf"],
    ]
    completion_events = [event for event in events if "份课表" in str(event.get("message") or "")]
    assert [event["source_id"] for event in completion_events] == [
        row_ids["张三-中方课表.pdf"],
        row_ids["李四-英方课表.pdf"],
    ]


def test_retry_after_worker_restart_explains_paths_are_gone(tmp_path: Path):
    pdf = tmp_path / "张三-中方课表.pdf"
    pdf.write_bytes(_pdf_bytes())

    db_path = tmp_path / "state.sqlite3"
    store = StateStore(db_path)
    job_id = store.create_job({"paths": [str(pdf)]}, "sig", [str(pdf)])
    store.mark_running_jobs_interrupted()

    # A worker restart builds a fresh StateStore; the source paths stay out of the
    # database by design, so the retry entry must explain the situation instead of
    # claiming that no failed file exists.
    restarted = StateStore(db_path)
    coordinator = JobCoordinator(RecordingCore(), restarted)
    assert restarted.failed_paths(job_id) == []

    with pytest.raises(ValueError, match="没有保留本批课表的文件路径"):
        coordinator.retry_failed(job_id)


def test_retry_failed_reprocesses_the_original_batch_with_explicit_kind(tmp_path: Path):
    good = tmp_path / "张三-中方课表.pdf"
    good.write_bytes(_pdf_bytes())
    missing = tmp_path / "李四-英方课表.pdf"

    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = JobCoordinator(RecordingCore(), store)
    started = coordinator.start(
        {"paths": [str(good), str(missing)], "explicit_kind": "中方"},
        background=False,
    )
    failed_paths = store.failed_paths(started["job_id"])
    assert failed_paths == [str(missing)]

    missing.write_bytes(_pdf_bytes())
    retried = coordinator.retry_failed(started["job_id"], background=False)
    retried_job = store.get_job(retried["job_id"])
    assert retried_job["request"]["explicit_kind"] == "中方"
    assert retried_job["request"]["retry_of"] == started["job_id"]
    assert retried_job["request"]["file_count"] == 2
    assert retried_job["status"] == "completed"
    assert [row["file_name"] for row in retried_job["files"]] == [
        "张三-中方课表.pdf", "李四-英方课表.pdf"
    ]
    assert all(row["status"] == "completed" for row in retried_job["files"])
    assert {source["file_name"] for source in retried_job["result"]["generation"]["sources"]} == {
        "张三-中方课表.pdf", "李四-英方课表.pdf"
    }


def test_safe_error_message_redacts_absolute_paths_only():
    from app.privacy import safe_error_message

    cases = [
        r"读取失败：C:\Users\member\Documents\张三课表.pdf",
        r"[Errno 2] No such file or directory: 'D:\数据\a b.pdf'",
        r"\\server\share\a.pdf 无法访问",
        "/home/member/a.pdf 不存在",
    ]
    for case in cases:
        message = safe_error_message(case)
        assert "C:\\Users" not in message
        assert "D:\\" not in message
        assert "\\\\server" not in message
        assert "/home/member" not in message
        assert "[本地文件]" in message


def _saved_review_job(tmp_path: Path, *, issue=None):
    from dataclasses import asdict

    from core import schedule_core as real_schedule_core

    member_key = "成员甲｜办公室｜干事"
    file_names = ["成员甲-中方课表.pdf", "成员甲-英方课表.pdf"]
    paths = [str(tmp_path / "first" / file_names[0]), str(tmp_path / "second" / file_names[1])]
    hashes = ["hash-cn", "hash-en"]
    blocks = []
    for index, (kind, period) in enumerate((("中方", 1), ("英方", 2))):
        blocks.append(
            {
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
        )
    issues = [asdict(issue)] if issue is not None else []
    state = "needs_review" if issue is not None else "accepted"
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
        "quality_state": state,
        "issues": issues,
        "corrections": [],
        "parser_signature": "audit-signature",
        "warnings": [],
        "errors": [],
    }
    store = StateStore(tmp_path / "state.sqlite3")
    job_id = store.create_job({}, "audit-signature", paths)
    for path, source_hash in zip(paths, hashes):
        store.update_file(job_id, path, source_hash=source_hash)
    store.save_job_result(
        job_id,
        result_json=json.dumps(snapshot, ensure_ascii=False),
        error="",
        quality_state=state,
        current=2,
        total=2,
        issues=issues,
    )
    return store, job_id, real_schedule_core


def test_apply_correction_rolls_back_correction_when_snapshot_save_fails(tmp_path: Path, monkeypatch):
    from app.services.review_service import apply_correction

    store, job_id, real_schedule_core = _saved_review_job(tmp_path)
    monkeypatch.setattr(
        store,
        "save_job_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk error")),
    )

    with pytest.raises(RuntimeError):
        apply_correction(
            store=store,
            schedule_core=real_schedule_core,
            job_id=job_id,
            block_id="block-0",
            field="course",
            new_value="修正后的课程",
            reason="原文核对",
            operator_id="测试者",
        )

    assert store.corrections_for_source("hash-cn", "audit-signature") == []
    assert store.get_job(job_id)["result"]["generation"]["blocks"][0]["course"] == "示例课程0"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE event='review.corrected'").fetchone()[0] == 0


def test_confirm_issue_rolls_back_flag_when_snapshot_save_fails(tmp_path: Path, monkeypatch):
    from core.models import ParseIssue
    from app.services.review_service import confirm_issue

    issue = ParseIssue(
        code="MEMBER_IDENTITY_INCOMPLETE",
        message="缺少部门或角色",
        severity="warning",
        field="member_key",
        blocks_export=True,
    )
    store, job_id, _ = _saved_review_job(tmp_path, issue=issue)
    monkeypatch.setattr(
        store,
        "update_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db error")),
    )

    with pytest.raises(RuntimeError):
        confirm_issue(store, job_id, str(issue.issue_id))

    job = store.get_job(job_id)
    assert job["quality_state"] == "needs_review"
    assert job["issues"][0]["confirmed"] is False

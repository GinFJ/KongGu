import copy
from datetime import datetime
import json
from types import SimpleNamespace

import pytest

from app.services.calendar_settings import load_calendar_settings, save_calendar_settings
from app.services.cloud_batch import _checksum, export_batch, import_batch
from app.services.desktop_workflow import export_excel, load_workflow_result
from app.services.job_service import JobCoordinator
from app.services.review_service import apply_correction, get_review_payload
from app.services.state_store import StateStore
from app.services.workspace_session import attach_originals, load_workspace
from core import schedule_core
from tests.course_verification_helpers import verify_test_job
from tests.test_e2e_workflow_real_core import _isolate_caches, _pair


def account(path):
    paths = SimpleNamespace(app_data_root=path)
    return StateStore.from_resource_paths(paths), paths


def make_batch(tmp_path, monkeypatch, *, complete=True, dates=None):
    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-03-02")
    monkeypatch.setenv("KONGGU_TEACHING_WEEKS", "18")
    _isolate_caches(monkeypatch, tmp_path)
    paths = list(_pair(tmp_path))
    store, guest = account(tmp_path / "guest")
    files = paths if complete else paths[:1]
    result = JobCoordinator(schedule_core, store).start(
        {"paths": list(map(str, files)), "workspace": True, "remember_sources": True, **(dates or {})}, background=False)
    job = result["job_id"]
    assert store.get_job(job)["status"] == "completed"
    return paths, store, guest, job


def test_date_review_account_roundtrip_keeps_gate_fingerprints_and_undo_data(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.services.date_review import update_date_review

    bounds = {"start_date": "2026-03-02", "end_date": "2026-03-06"}
    _, store, guest, job = make_batch(tmp_path, monkeypatch, dates=bounds)
    target, settings = account(tmp_path / "dated-account")
    def change(action, **fields):
        review = get_review_payload(store, job)
        return update_date_review(store, schedule_core, job, {
            "operation_id": str(uuid4()), "revision": review["revision"],
            "member_key": review["date_review"]["members"][0]["member_key"], **fields,
        }, action)

    review = get_review_payload(store, job)
    source = next(s["content_hash"] for s in review["sources"] if s["kind"] == "中方")
    change("slot", source_hash=source, date="2026-03-02", period=1, marked=False, page=0)
    saved = change("slot", source_hash="manual", date="2026-03-04", period=10, marked=True, page=0)
    assert saved["date_review"]["log"][-1]["page"] is None
    doc = export_batch(store, job, guest)
    assert doc["format"] == "konggu-batch-v2" and "removed_slots" in doc["snapshot"]["date_review"]
    imported = import_batch(target, doc, settings)["job_id"]
    assert target.get_job(imported)["request"]["end_date"] == bounds["end_date"]
    assert not get_review_payload(target, imported)["date_review"]["members"][0]["confirmed"]
    workflow = load_workflow_result(imported, target, schedule_core)
    with pytest.raises(ValueError):
        export_excel(schedule_core=schedule_core, workflow_result=workflow, target_path=str(tmp_path / "pending.xlsx"))

    final = change("member", acknowledge_missing=True)["date_review"]
    snapshot = store.get_job(job)["result"]
    state = snapshot["date_review"]
    state["raw_local_notes"] = str(tmp_path)
    state["log"][0]["source_path"] = str(tmp_path / "原件.pdf")
    next(iter(state["removed_slots"].values()))[0]["private_ocr"] = "PRIVATE_OCR"
    store.update_job(job, result_json=json.dumps(snapshot))
    doc = export_batch(store, job, guest)
    assert "PRIVATE_OCR" not in json.dumps(doc) and str(tmp_path) not in json.dumps(doc, ensure_ascii=False)
    imported = import_batch(target, doc, settings)["job_id"]
    assert get_review_payload(target, imported)["date_review"] == final
    assert final["members"][0]["confirmed"]
    assert export_batch(target, imported, settings)["snapshot"]["date_review"] == doc["snapshot"]["date_review"]

    # Check both a local 0.13.0 snapshot and an already-uploaded backup.
    # Only the manual event used page zero to mean that no PDF was involved.
    state["log"][1]["page"] = 0
    store.update_job(job, result_json=json.dumps(snapshot))
    assert export_batch(store, job, guest) == doc
    legacy = copy.deepcopy(doc)
    legacy["snapshot"]["date_review"]["log"][1]["page"] = 0
    legacy["checksum"] = _checksum({k: v for k, v in legacy.items() if k != "checksum"})
    legacy_store, legacy_settings = account(tmp_path / "legacy-restore")
    restored_job = import_batch(legacy_store, legacy, legacy_settings)["job_id"]
    restored_view = get_review_payload(legacy_store, restored_job)["date_review"]
    assert restored_view == final and restored_view["log"][0]["page"] == 0
    assert export_batch(legacy_store, restored_job, legacy_settings)["snapshot"]["date_review"] == doc["snapshot"]["date_review"]
    workflow = load_workflow_result(imported, target, schedule_core)
    assert export_excel(schedule_core=schedule_core, workflow_result=workflow, target_path=str(tmp_path / "dated.xlsx")).is_file()
    rejected = copy.deepcopy(doc)
    rejected["format"] = "konggu-batch-v1"
    rejected["checksum"] = _checksum({k: v for k, v in rejected.items() if k != "checksum"})
    with pytest.raises(ValueError):
        import_batch(target, rejected, settings)


def test_dated_flags_survive_account_restore_and_guard_member_confirmation(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.services.date_review import update_date_review

    _, store, guest, job = make_batch(tmp_path, monkeypatch, dates={"start_date": "2026-03-02", "end_date": "2026-03-06"})
    target, settings = account(tmp_path / "flag-account")
    initial = get_review_payload(store, job)
    owner = initial["date_review"]["members"][0]["member_key"]
    digest = next(s["content_hash"] for s in initial["sources"] if s["kind"] == "中方")
    place = dict(source_hash=digest, date="2026-03-02", period=1, page=0, bbox=[1, 2, 8, 11],
                 page_width=100, page_height=120)

    def change(action, **fields):
        revision = get_review_payload(store, job)["revision"]
        return update_date_review(store, schedule_core, job, dict(operation_id=str(uuid4()), revision=revision,
                                  member_key=owner, **fields), action)["date_review"]

    opened = change("flag", open=True, **place)
    doc = export_batch(store, job, guest)
    imported = import_batch(target, doc, settings)["job_id"]
    assert get_review_payload(target, imported)["date_review"] == opened
    for mutation in ({"source_hash": "manual", "page": None}, {"source_hash": "0"*64}, {"period": 0},
                     {"page": 99}, {"bbox": [1, 2, 101, 11]}, {"open": False}):
        invalid = copy.deepcopy(doc)
        invalid["snapshot"]["date_review"]["log"][-1].update(mutation)
        invalid["checksum"] = _checksum({k: v for k, v in invalid.items() if k != "checksum"})
        with pytest.raises(ValueError):
            import_batch(target, invalid, settings)

    change("flag", open=False, **place)
    confirmed = change("member", acknowledge_missing=True)
    doc = export_batch(store, job, guest)
    imported = import_batch(target, doc, settings)["job_id"]
    assert get_review_payload(target, imported)["date_review"] == confirmed
    assert confirmed["members"][0]["confirmed"]
    invalid = copy.deepcopy(doc)
    del invalid["snapshot"]["date_review"]["log"][1]
    invalid["checksum"] = _checksum({k: v for k, v in invalid.items() if k != "checksum"})
    with pytest.raises(ValueError):
        import_batch(target, invalid, settings)


def test_saved_course_checks_corrected_data_and_semester_roundtrip(tmp_path, monkeypatch):
    paths, guest_store, guest, job = make_batch(tmp_path, monkeypatch)
    before = get_review_payload(guest_store, job)
    apply_correction(store=guest_store, schedule_core=schedule_core, job_id=job,
                     block_id=before["blocks"][0]["block_id"], field="course", new_value="合成测试课程",
                     reason="已对照合成原件", operator_id="测试账号")
    checked = verify_test_job(guest_store, job)
    target, account_paths = account(tmp_path / "account")
    other_calendar = {"semester_start_date": "2026-09-07", "teaching_weeks": 20}
    save_calendar_settings(account_paths, other_calendar)
    doc = export_batch(guest_store, job, guest)
    assert str(tmp_path) not in json.dumps(doc, ensure_ascii=False)
    assert doc["calendar"]["teaching_weeks"] == 18
    output = import_batch(target, doc, account_paths)
    imported = output["job_id"]
    assert imported != job and load_calendar_settings(account_paths) == doc["calendar"]
    assert load_workspace(guest_store)["job_id"] == job
    new_workspace = load_workspace(target)
    assert new_workspace["job_id"] == imported and all(not f["path"] for f in new_workspace["files"])
    review = get_review_payload(target, imported)
    assert review["verification"] == checked["verification"]
    assert len(review["corrections"]) == 1 and review["corrections"][0]["reason"] == "已对照合成原件"
    workflow = load_workflow_result(imported, target, schedule_core)
    assert workflow.process_result.quality_state == "accepted"
    assert export_excel(schedule_core=schedule_core, workflow_result=workflow,
                        target_path=str(tmp_path / "restored.xlsx")).is_file()
    twice = import_batch(target, doc, account_paths)
    assert twice["job_id"] == imported
    assert export_batch(target, imported, account_paths)["batch_id"] == doc["batch_id"]
    attach_originals(target, imported, list(map(str, paths)), False)
    assert all(f["path"] for f in load_workspace(target)["files"])


def test_corrupt_extra_paths_and_export_gate_are_rejected_atomically(tmp_path, monkeypatch):
    _, original, guest, job = make_batch(tmp_path, monkeypatch, complete=False)
    doc = export_batch(original, job, guest)
    target, account_paths = account(tmp_path / "account")
    imported = import_batch(target, doc, account_paths)["job_id"]
    assert target.get_job(imported)["quality_state"] == "blocked"
    samples = []
    bad_checksum = copy.deepcopy(doc)
    bad_checksum["batch_id"] = "bad"
    samples.append(bad_checksum)
    injected = copy.deepcopy(doc)
    injected["snapshot"]["generation"]["sources"][0]["source_path"] = str(tmp_path / "other.pdf")
    unblocked = copy.deepcopy(doc)
    unblocked["snapshot"].update(issues=[], quality_state="accepted")
    error_confirmed = copy.deepcopy(doc)
    next(i for i in error_confirmed["snapshot"]["issues"] if i["severity"] == "error")["confirmed"] = True
    for bad in (injected, unblocked, error_confirmed):
        bad["checksum"] = _checksum({k: v for k, v in bad.items() if k != "checksum"})
        samples.append(bad)
    for bad in samples:
        with pytest.raises(ValueError):
            import_batch(target, bad, account_paths)
        assert load_workspace(target)["job_id"] == imported
    assert len(get_review_payload(target, imported)["verification"]["items"]) > 0


def test_draft_incompatible_versions_and_private_metadata(tmp_path, monkeypatch):
    _, store, guest, job = make_batch(tmp_path, monkeypatch)
    snapshot = store.get_job(job)["result"]
    snapshot["generation"]["blocks"][0]["provenance"] = {"raw_ocr": "MUST-NOT-UPLOAD", "image_bytes": "MUST-NOT-UPLOAD"}
    snapshot["generation"]["pdf_inspections"][0]["log_text"] = "MUST-NOT-UPLOAD"
    snapshot.update(warnings=["MUST-NOT-UPLOAD"], errors=["MUST-NOT-UPLOAD"])
    store.update_job(job, result_json=json.dumps(snapshot))
    doc = export_batch(store, job, guest)
    assert "MUST-NOT-UPLOAD" not in json.dumps(doc)
    other, settings = account(tmp_path / "target")
    doc["snapshot"]["parser_signature"] = "old"
    doc["checksum"] = _checksum({k: v for k, v in doc.items() if k != "checksum"})
    with pytest.raises(ValueError, match="不兼容"):
        import_batch(other, doc, settings)
    assert load_workspace(other)["files"] == []
    draft = store.create_job({}, "sig", ["draft.pdf"])
    with pytest.raises(ValueError, match="完成"):
        export_batch(store, draft, guest)

from tests.test_department_exports import departments


def test_namesakes_manual_changes_and_scoped_workbooks_roundtrip(departments, tmp_path, monkeypatch):
    from uuid import uuid4
    from openpyxl import load_workbook
    from app.services.review_operations import mutate_courses
    from app.services.desktop_workflow import serialize_workflow_result
    from core.signature import build_parser_signature

    store, job = departments
    sig = build_parser_signature()
    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", sig.calendar_start)
    monkeypatch.setenv("KONGGU_TEACHING_WEEKS", sig.teaching_weeks)
    guest = SimpleNamespace(app_data_root=tmp_path / "guest-settings")
    save_calendar_settings(guest, {"semester_start_date": sig.calendar_start, "teaching_weeks": int(sig.teaching_weeks)})
    review = get_review_payload(store, job)

    def change(source, action, **fields):
        nonlocal review
        request = {"action": action, "operation_id": str(uuid4()), "revision": review["revision"],
                   "source_hash": source, "reason": "合成原件复核", **fields}
        review = mutate_courses(store=store, schedule_core=schedule_core, job_id=job, request=request)

    source = review["sources"][0]["content_hash"]
    change(source, "add", course={"course": "Systems/Networks and C/C++", "week": 1,
                                  "weekday": "周一", "periods": [12, 13], "page": 0})
    added_id = next(b["block_id"] for b in review["blocks"] if "C/C++" in b["course"])
    change(source, "delete", block_id=added_id)
    change(source, "restore", block_id=added_id)
    # Both originals were checked; this member remains on the roster after their last class is removed.
    for block in list(review["blocks"]):
        if block["name"] == "成员乙":
            change(block["source_hash"], "delete", block_id=block["block_id"])
    checked = verify_test_job(store, job)
    before = load_workflow_result(job, store, schedule_core)
    target, account_paths = account(tmp_path / "cloud-target")
    imported = import_batch(target, export_batch(store, job, guest), account_paths)["job_id"]
    after = load_workflow_result(imported, target, schedule_core)
    assert get_review_payload(target, imported)["verification"] == checked["verification"]
    assert after.generation_result.occupancy == before.generation_result.occupancy
    assert after.generation_result.weeks == before.generation_result.weeks == [1, 2]
    assert after.generation_result.students == before.generation_result.students
    assert "成员乙｜宣传部｜干事" in after.generation_result.students
    scopes = serialize_workflow_result(after, imported)["department_scopes"]
    assert scopes == serialize_workflow_result(before, job)["department_scopes"]
    assert len(get_review_payload(target, imported)["corrections"]) == len(after.process_result.corrections)
    class ExportClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 2, 9, 0, tzinfo=tz)

    # The audit sheet records the wall-clock time of each individual export.
    monkeypatch.setattr(schedule_core, "datetime", ExportClock)
    for dept in ("宣传部", "办公室"):
        outputs = []
        for index, flow in enumerate((before, after)):
            path = export_excel(schedule_core=schedule_core, workflow_result=flow,
                                target_path=str(tmp_path / f"dept-{dept}-{index}.xlsx"), department=dept, week=1)
            book = load_workbook(path, read_only=True)
            try:
                outputs.append({sheet.title: list(sheet.values) for sheet in book})
            finally:
                book.close()
        assert outputs[0] == outputs[1]

"""Local batch history across restarts, calendar changes and data cleanup."""
import base64
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.services.calendar_settings import load_calendar_settings, save_calendar_settings
from app.services.desktop_workflow import export_excel, load_workflow_result
from app.services.job_service import JobCoordinator
from app.services.review_service import get_review_payload
from app.services.state_store import StateStore
from app.services.workspace_session import (
    attach_originals, count_history, list_history, load_workspace, open_history, read_bound_pdf, save_draft,
)
from core import schedule_core
from core.legacy_adapter import hash_file
from tests.test_date_review import action
from tests.test_e2e_workflow_real_core import _isolate_caches, _pair
from tests.test_workspace_session import complete


def test_history_lists_batches_and_preserves_each_selection(tmp_path):
    store = StateStore(tmp_path / "state.db")
    originals = [tmp_path / f"member-{i}.pdf" for i in range(5)]
    for path in originals:
        path.write_bytes(f"%PDF {path.name}".encode())
    first = complete(store, originals[:4])
    second = complete(store, originals[4:], remember=False)
    save_draft(store, [], False, start_date="2026-03-02", end_date="2026-03-08")
    restarted = StateStore(store.path)
    history = list_history(restarted)
    assert history["draft"] and count_history(restarted) == 2
    assert [batch["job_id"] for batch in history["batches"]] == [second, first]
    assert history["batches"][1]["file_names"] == [p.name for p in originals[:3]]
    assert history["batches"][1]["file_count"] == 4
    assert str(tmp_path) not in json.dumps(history)
    assert open_history(restarted, first)["files"] == [
        {"name": p.name, "path": str(p)} for p in originals[:4]]
    assert not list_history(restarted)["draft"]
    pdf = read_bound_pdf(restarted, first, hash_file(originals[0]), originals[0].name)
    assert base64.b64decode(pdf["data"]) == originals[0].read_bytes()
    assert open_history(restarted, second)["files"] == [{"name": originals[4].name, "path": ""}]
    attach_originals(restarted, second, [str(originals[4])], False)
    restarted = StateStore(store.path)
    assert not open_history(restarted, second)["remember"]
    assert not load_workspace(restarted)["files"][0]["path"]
    with restarted.connect() as db:
        assert str(originals[4]) not in db.execute(
            "SELECT selection FROM local_batch_history WHERE job_id=?", (second,)).fetchone()[0]
    pending = restarted.create_job({}, "sig", [])
    other = StateStore(tmp_path / "different-account.db")
    for source, bad_id in [(restarted, pending), (restarted, "unknown"), (other, first)]:
        with pytest.raises(ValueError, match="找不到"):
            open_history(source, bad_id)
    assert load_workspace(restarted)["job_id"] == second
    assert not count_history(other)
    restarted.clear_all_data()
    cleared = StateStore(store.path)
    assert list_history(cleared) == {"ok": True, "batches": [], "draft": False}
    assert not count_history(cleared) and not load_workspace(cleared)["files"]
    assert all(p.is_file() for p in originals)


def test_migrate_preexisting_history_keep_results_and_expire_locations(tmp_path):
    original = tmp_path / "synthetic.pdf"
    original.write_bytes(b"%PDF synthetic")
    store = StateStore(tmp_path / "legacy.db")
    keep = complete(store, [original], legacy=True)
    legacy = complete(store, [original], legacy=True)
    new = complete(store, [original])
    failed = store.create_job({"workspace": True}, "sig", [str(original)])
    store.update_job(failed, status="failed")
    recent = (datetime.now(UTC) - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    expired = "2000-01-01T00:00:00Z"
    selection = json.dumps([{"name": original.name, "path": str(original)}])
    with store.connect() as db:
        # The previous release had a single pointer, without a history entry.
        db.execute("UPDATE workspace SET job_id=?,selection=?,remember=1,updated_at=? WHERE id=1",
                   (keep, selection, recent))
    restarted = StateStore(store.path)
    with restarted.connect() as db:
        assert tuple(db.execute("SELECT keep_result,paths_updated_at FROM local_batch_history WHERE job_id=?",
                                (keep,)).fetchone()) == (0, recent)
    assert open_history(restarted, keep)["files"][0]["path"] == str(original)
    with restarted.connect() as db:
        assert tuple(db.execute("SELECT keep_result,paths_updated_at FROM local_batch_history WHERE job_id=?",
                                (keep,)).fetchone()) == (1, recent)
        db.execute("UPDATE jobs SET updated_at=?", (expired,))
        db.execute("UPDATE workspace SET updated_at=?", (expired,))
        db.execute("UPDATE local_batch_history SET paths_updated_at=?", (expired,))
    digest = hash_file(original)
    for source in [digest, "unrelated"]:
        restarted.add_correction(source_hash=source, block_id="b1", field="course", original_value="A",
            new_value="B", reason="test", parser_signature="sig", operator_id="tester")
    with restarted.connect() as db:
        db.execute("UPDATE corrections SET created_at=?", (expired,))
    reopened = StateStore(store.path)
    assert {batch["job_id"] for batch in list_history(reopened)["batches"]} == {keep, new}
    assert not reopened.get_job(legacy) and not reopened.get_job(failed)
    assert open_history(reopened, keep)["files"] == [{"name": original.name, "path": ""}]
    with reopened.connect() as db:
        assert not any(str(original) in selection for selection, in db.execute("SELECT selection FROM local_batch_history"))
    assert reopened.get_job(keep)["result"]["verified_items"] == ["receipt"]
    assert len(reopened.corrections_for_source(digest, "sig")) == 1
    assert not reopened.corrections_for_source("unrelated", "sig")


@pytest.mark.parametrize("old_format", [False, True])
def test_reopen_confirmed_dated_batch_and_export_without_originals(tmp_path, monkeypatch, old_format):
    _isolate_caches(monkeypatch, tmp_path)
    monkeypatch.setenv("KONGGU_SEMESTER_START_DATE", "2026-03-02")
    monkeypatch.setenv("KONGGU_TEACHING_WEEKS", "18")
    resource = SimpleNamespace(app_data_root=tmp_path)
    spring = {"semester_start_date": "2026-03-02", "teaching_weeks": 16}
    save_calendar_settings(resource, spring)
    paths = _pair(tmp_path)
    store = StateStore(tmp_path / "state.db")
    coordinator = JobCoordinator(schedule_core, store)
    bounds = dict(start_date="2026-03-05", end_date="2026-03-09")
    job = coordinator.start(dict(workspace=True, remember_sources=False, paths=list(map(str, paths)), **bounds),
                            background=False)["job_id"]
    member = get_review_payload(store, job)["date_review"]["members"][0]["member_key"]
    action((store, job, member), "member", acknowledge_missing=True)
    for path in paths:
        path.unlink()
    if old_format:
        with store.connect() as db:
            db.execute("UPDATE jobs SET request_json=json_remove(request_json,'$.workspace_calendar') WHERE id=?", (job,))
    save_calendar_settings(resource, {"semester_start_date": "2026-09-07", "teaching_weeks": 20})
    restarted = StateStore(store.path)
    item = list_history(restarted)["batches"][0]
    assert {key: item[key] for key in bounds} == bounds
    workspace = open_history(restarted, job, resource)
    assert workspace["calendar"] == load_calendar_settings(resource) == spring
    assert {key: workspace[key] for key in bounds} == bounds
    assert not any(file["path"] for file in workspace["files"])
    assert get_review_payload(restarted, job)["date_review"]["members"][0]["confirmed"]
    assert JobCoordinator(schedule_core, restarted).get(job)["job"]["output"]["can_export"]
    target = tmp_path / "reopened.xlsx"
    export_excel(schedule_core=schedule_core, workflow_result=load_workflow_result(job, restarted, schedule_core),
                 target_path=target)
    workbook = load_workbook(target, read_only=True)
    try:
        assert workbook.sheetnames
    finally:
        workbook.close()

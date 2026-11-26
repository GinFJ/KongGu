from pathlib import Path
import base64
import json

import pytest

from app.services.state_store import StateStore
from app.services.workspace_session import attach_originals, load_workspace, read_bound_pdf, save_draft
from core.legacy_adapter import hash_file


def complete(store, sources, remember=True, legacy=False):
    paths = [str(p) for p in sources]
    job = store.create_job({} if legacy else {"workspace": True, "remember_sources": remember}, "sig", paths)
    for path in sources:
        store.update_file(job, str(path), source_hash=hash_file(path), status="completed")
    store.save_job_result(job, result_json=json.dumps({"verified_items": ["receipt"]}), error="",
                          quality_state="needs_review", current=len(paths), total=len(paths), issues=[])
    store.clear_job_source_paths(job)
    return job


@pytest.mark.parametrize("remember", [False, True])
def test_draft_completed_batch_and_clear_survive_restart(tmp_path, remember):
    original = tmp_path / "sample.pdf"
    original.write_bytes(b"%PDF-1.4 synthetic")
    db = tmp_path / "data.sqlite3"
    store = StateStore(db)
    assert not load_workspace(store)["files"]
    save_draft(store, [str(original)], remember)
    draft = load_workspace(StateStore(db))
    assert not draft["job_id"]
    assert draft["files"] == [{"name": original.name, "path": str(original) if remember else ""}]
    job = complete(store, [original], remember)
    reopened = StateStore(db)
    saved = load_workspace(reopened)
    assert saved["job_id"] == job and saved["status"] == "completed"
    assert reopened.get_job(job)["result"]["verified_items"] == ["receipt"]
    if not remember:
        with pytest.raises(ValueError):
            read_bound_pdf(reopened, job, hash_file(original), original.name)
        attach_originals(reopened, job, [str(original)], False)
    restored = read_bound_pdf(reopened, job, hash_file(original), original.name)
    assert base64.b64decode(restored["data"]) == original.read_bytes()
    save_draft(reopened, [], False)
    assert load_workspace(StateStore(db))["files"] == []
    reopened.clear_all_data()
    assert load_workspace(StateStore(db))["files"] == [] and original.is_file()


def test_legacy_partial_relink_is_atomic_and_checks_every_digest(tmp_path):
    files = [tmp_path / name for name in ("A.pdf", "B.pdf")]
    for file in files:
        file.write_bytes(file.name.encode())
    db = tmp_path / "old.sqlite3"
    job = complete(StateStore(db), files, legacy=True)
    store = StateStore(db)
    assert not any(f["path"] for f in load_workspace(store)["files"])
    wrong = tmp_path / "moved" / "B.pdf"
    wrong.parent.mkdir()
    wrong.write_bytes(b"different")
    with pytest.raises(ValueError, match="不一致"):
        attach_originals(store, job, [str(files[0]), str(wrong)], True)
    assert not any(store.source_paths_for_job(job))
    attached = attach_originals(store, job, [str(files[0])], True)["files"]
    assert attached[0]["path"] and not attached[1]["path"]
    assert load_workspace(StateStore(db))["files"] == attached
    files[0].write_bytes(b"modified after binding")
    with pytest.raises(ValueError):
        read_bound_pdf(store, job, store.get_job(job)["files"][0]["source_hash"], files[0].name)
    assert not any(item["path"] for item in load_workspace(StateStore(db))["files"])


def test_selected_dates_survive_before_import_and_reject_invalid_edits(tmp_path):
    db = tmp_path / "draft.sqlite3"
    store = StateStore(db)
    dates = {"start_date": "2026-11-28", "end_date": "2026-12-04"}
    save_draft(store, [], True, **dates)
    for invalid in ["2026-11-15", "2026-19-99"]:
        with pytest.raises(ValueError, match="日期"):
            save_draft(store, [], True, dates["start_date"], invalid)
    reopened = load_workspace(StateStore(db))
    assert {key: reopened[key] for key in dates} == dates
    save_draft(store, [], False)
    assert "start_date" not in load_workspace(store)


def test_partial_and_full_draft_relinks_keep_cross_month_dates_after_restart(tmp_path):
    db = tmp_path / "draft-relink.sqlite3"
    dates = dict(start_date="2026-11-30", end_date="2026-12-03")
    originals = [tmp_path / name for name in ("A.pdf", "B.pdf")]
    for original in originals:
        original.write_bytes(b"anonymous original")
    save_draft(StateStore(db), [p.name for p in originals], False, **dates)
    for count, original in enumerate(originals, 1):
        attach_originals(StateStore(db), "", [str(original)], True)
        workspace = load_workspace(StateStore(db))
        assert {k: workspace[k] for k in dates} == dates
        assert [bool(f["path"]) for f in workspace["files"]] == [i < count for i in range(2)]
    store = StateStore(db)
    job = store.create_job(dict(workspace=True, remember_sources=True, **dates), "sig", list(map(str, originals)))
    for path in originals:
        store.update_file(job, str(path), source_hash=hash_file(path), status="completed")
    store.save_job_result(job, result_json=json.dumps({"verified_items":["approved"]}), error="",
                          quality_state="accepted", current=2, total=2, issues=[])
    attach_originals(StateStore(db), job, [str(originals[0])], True)
    reopened = StateStore(db)
    workspace = load_workspace(reopened)
    assert workspace["job_id"] == job and {k: workspace[k] for k in dates} == dates
    assert reopened.get_job(job)["result"]["verified_items"] == ["approved"]


def test_interrupted_batch_and_expiration(tmp_path):
    path = tmp_path / "draft.pdf"
    path.write_bytes(b"draft")
    db = tmp_path / "interrupted.sqlite3"
    store = StateStore(db)
    store.create_job({"workspace": True, "remember_sources": True}, "sig", [str(path)])
    reopened = StateStore(db)
    assert load_workspace(reopened)["status"] == "interrupted"
    with reopened.connect() as sql:
        sql.execute("UPDATE workspace SET updated_at='2000-01-01T00:00:00Z'")
        sql.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00Z'")
    assert not load_workspace(StateStore(db))["files"]

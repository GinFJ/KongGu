"""Measured cache and record sizes and cleanup operate on the same local profile."""
from dataclasses import replace

import pytest

from app import offline_resources
from app.services.job_service import JobCoordinator
from app.services.state_store import StateStore
from app.sidecar import dispatch


def test_cache_size_clear_and_history_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "home"))
    paths = offline_resources.resolve_resource_paths()
    store = StateStore.from_resource_paths(paths)
    coordinator = JobCoordinator(None, store)
    other = tmp_path / "second"
    scoped = replace(paths, app_data_root=other, cache_root=other / "cache", logs_root=other / "logs")
    other_store = StateStore.from_resource_paths(scoped)
    other_coordinator = JobCoordinator(None, other_store)
    original = tmp_path / "synthetic.pdf"
    original.write_bytes(b"original")
    exported = tmp_path / "output.xlsx"
    exported.write_bytes(b"test-export")
    job = store.create_job({"workspace": True}, "signature", [str(original)])
    store.save_job_result(job, result_json='{"fixture":true}', error="", quality_state="needs_review",
                          current=1, total=1, issues=[])
    for base in (paths.cache_root, scoped.cache_root):
        for name in ("pdf_text", "parsed_blocks"):
            folder = base / name / "nested"
            folder.mkdir(parents=True)
            (folder / "entry").write_bytes(b"1234567")
    model = paths.user_resources_root / "recognition.data"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"shared-resource")
    request = lambda name: dispatch({"command": name}, coordinator, account_paths=paths)
    before = request("data.usage")
    assert before["cache_bytes"] == 14 and before["cache_files"] == 2 and before["batch_count"] == 1
    assert before["history_bytes"] >= store.path.stat().st_size > 0
    assert set(request("data.clear_cache")["cleared_cache"]) == {"pdf_text", "parsed_blocks"}
    after = request("data.usage")
    assert after["cache_bytes"] == 0 and after["batch_count"] == 1
    assert StateStore.from_resource_paths(paths).get_job(job)["result"] == {"fixture": True}
    assert dispatch({"command": "data.usage"}, other_coordinator, account_paths=scoped)["cache_bytes"] == 14
    request("data.clear")
    assert request("data.usage")["batch_count"] == 0
    assert original.read_bytes() == b"original" and exported.read_bytes() == b"test-export"
    assert model.read_bytes() == b"shared-resource"


def test_repeated_cache_roots_count_once_and_block_while_generating(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path))
    paths = offline_resources.resolve_resource_paths()
    monkeypatch.setattr(offline_resources, "__file__", str(tmp_path / "app" / "offline_resources.py"))
    file = paths.cache_root / "pdf_layout" / "item"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"duplicate-root")
    assert offline_resources.processing_cache_usage(paths, include_legacy=True) == {
        "cache_bytes": file.stat().st_size, "cache_files": 1,
    }
    coordinator = JobCoordinator(None, StateStore.from_resource_paths(paths))
    monkeypatch.setattr(coordinator, "has_active_job", lambda: True)
    for command in ("data.clear_cache", "data.clear", "workspace.open"):
        with pytest.raises(ValueError, match="处理中"):
            dispatch({"command": command}, coordinator, account_paths=paths)
    assert file.exists()

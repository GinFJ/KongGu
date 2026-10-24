"""Job coordinator caches the restored/serialized result between polls."""

import pytest

import app.services.job_service as job_module
from app.services.state_store import StateStore


def test_get_reuses_the_restored_result_between_polls(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = job_module.JobCoordinator(object(), store)
    job_id = store.create_job({}, "test-signature", [])
    store.save_job_result(
        job_id,
        result_json='{"version": 1}',
        error="",
        quality_state="accepted",
        current=1,
        total=1,
        issues=[],
    )

    restored: list[dict] = []

    def fake_restore(result, core):
        restored.append(result)
        return object()

    monkeypatch.setattr(job_module, "restore_workflow", fake_restore)
    monkeypatch.setattr(
        job_module,
        "serialize_workflow_result",
        lambda workflow, ref: {"result_ref": ref},
    )

    first = coordinator.get(job_id)
    second = coordinator.get(job_id)

    # 650ms 轮询与完成事件会重复拉取同一结果：只 restore 一次。
    assert len(restored) == 1
    assert first["job"]["output"] == {"result_ref": job_id}
    assert second["job"]["output"] == {"result_ref": job_id}


def test_cache_invalidates_when_the_saved_result_changes(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = job_module.JobCoordinator(object(), store)
    job_id = store.create_job({}, "test-signature", [])
    store.save_job_result(
        job_id,
        result_json='{"version": 1}',
        error="",
        quality_state="accepted",
        current=1,
        total=1,
        issues=[],
    )
    restored: list[dict] = []

    def fake_restore(result, core):
        restored.append(result)
        return object()

    monkeypatch.setattr(job_module, "restore_workflow", fake_restore)
    monkeypatch.setattr(
        job_module,
        "serialize_workflow_result",
        lambda workflow, ref: {"result_ref": ref, "version": 2},
    )

    coordinator.get(job_id)
    store.save_job_result(
        job_id,
        result_json='{"version": 2}',
        error="",
        quality_state="accepted",
        current=1,
        total=1,
        issues=[],
    )
    coordinator.get(job_id)

    # 人工修正后会写回新的 result：缓存必须按内容失效。
    assert len(restored) == 2

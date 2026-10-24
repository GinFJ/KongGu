"""sidecar 协议入口的数量校验：必须在写库/读取文件之前拒绝。"""

import json

import pytest

from app.offline_resources import configure_offline_environment
from app.services.desktop_workflow import MAX_INPUT_FILES
from app.sidecar import _acquire_worker_slot, dispatch


def test_dispatch_rejects_too_many_paths_before_creating_a_job():
    paths = [f"C:/fake/{index}.pdf" for index in range(MAX_INPUT_FILES + 1)]

    with pytest.raises(ValueError, match="最多处理"):
        dispatch({"command": "job.start", "paths": paths})


def test_dispatch_rejects_empty_job_start():
    with pytest.raises(ValueError, match="请先选择课表"):
        dispatch({"command": "job.start", "paths": []})


def test_dispatch_rejects_too_many_paths_in_nested_payload():
    paths = [f"C:/fake/{index}.pdf" for index in range(MAX_INPUT_FILES + 1)]

    with pytest.raises(ValueError, match="最多处理"):
        dispatch({"command": "job.start", "payload": {"paths": paths}})


def test_worker_slot_is_exclusive_per_user(tmp_path, monkeypatch):
    import app.sidecar as sidecar_module

    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))
    paths = configure_offline_environment()

    # 释放上一个测试可能留下的句柄，模拟干净启动。
    sidecar_module._WORKER_LOCK_HANDLE = None

    assert _acquire_worker_slot(paths) is True
    # 同一用户第二个 worker 必须被拒绝：否则新进程会把存活任务标成 interrupted。
    assert _acquire_worker_slot(paths) is False

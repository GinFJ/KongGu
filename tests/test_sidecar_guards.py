"""sidecar 协议入口的数量校验：必须在写库/读取文件之前拒绝。"""

import pytest

from app.services.desktop_workflow import MAX_INPUT_FILES
from app.sidecar import dispatch


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

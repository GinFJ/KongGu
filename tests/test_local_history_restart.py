"""Two independent desktop worker lifetimes share only a temporary local data directory."""
from pathlib import Path
import time
from uuid import uuid4

from openpyxl import load_workbook

from tests.test_cloud_desktop_session import desktop
from tests.test_e2e_workflow_real_core import _pair


def test_closed_app_can_open_either_local_batch_and_clear_only_cache(tmp_path):
    profile = tmp_path / "app-data"
    files = [str(path) for path in _pair(tmp_path)]
    with desktop(profile) as request:
        assert request("workspace.history")["batches"] == []
        batches = []
        for index, (start, end) in enumerate((("2026-03-02", "2026-03-06"), ("2026-03-09", "2026-03-13"))):
            job = request("job.start", {"paths": files, "workspace": True, "remember_sources": True,
                                        "start_date": start, "end_date": end})["job_id"]
            deadline = time.monotonic() + 150
            while True:
                status = request("job.get", {"job_id": job})["job"]["status"]
                if status == "completed":
                    break
                assert status in {"queued", "running", "review"} and time.monotonic() < deadline, status
                time.sleep(.1)
            batches.append(job)
            if index == 0:
                review = request("review.get", {"job_id": job})
                member = review["date_review"]["members"][0]
                review = request("review.member", {"job_id": job, "member_key": member["member_key"],
                                                   "revision": review["revision"], "operation_id": str(uuid4()),
                                                   "acknowledge_missing": True})
                assert review["date_review"]["members"][0]["confirmed"]

    with desktop(profile) as reopened:
        history = reopened("workspace.history")
        assert {entry["job_id"] for entry in history["batches"]} == set(batches)
        assert {entry["start_date"] for entry in history["batches"]} == {"2026-03-02", "2026-03-09"}
        for job in reversed(batches):
            saved = reopened("workspace.open", {"job_id": job})
            assert saved["job_id"] == job
            assert [entry["path"] for entry in saved["files"]] == files
        cache = profile / "cache" / "pdf_text" / "history-qa.txt"
        cache.parent.mkdir(exist_ok=True, parents=True)
        cache.write_bytes(b"synthetic-cache")
        before = reopened("data.usage")
        assert before["cache_bytes"] >= 15 and before["cache_files"] >= 1 and before["batch_count"] == 2
        reopened("data.clear_cache")
        assert not cache.exists()
        after = reopened("data.usage")
        assert (after["cache_bytes"], after["cache_files"], after["batch_count"]) == (0, 0, 2)
        assert reopened("review.get", {"job_id": batches[0]})["date_review"]["members"][0]["confirmed"]
        output = tmp_path / "restored.xlsx"
        reopened("exports.excel", {"result_ref": batches[0], "target_path": str(output)})
        workbook = load_workbook(output, read_only=True)
        try:
            assert len(workbook.sheetnames) >= 1
        finally:
            workbook.close()
        reopened("data.clear")

    with desktop(profile) as empty:
        assert empty("workspace.history")["batches"] == []
        assert empty("data.usage")["cache_bytes"] == 0
        assert output.is_file() and all(Path(file).is_file() for file in files)

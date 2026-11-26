"""Round-trip the desktop protocol with separate data roots and the actual HTTP service."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

from openpyxl import load_workbook
import pytest

from app.services.cloud_transport import normalize_endpoint
from tests.test_e2e_workflow_real_core import _pair


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("address", ["http://example.com", "https://name:pwd@example.com", "file://localhost",
                                    "https://a.test/v1", "https://a.test?secret=value", "http://127.0.0.1:0"])
def test_endpoint_rejects_ambiguous_credentials_or_external_plaintext(address):
    with pytest.raises(ValueError):
        normalize_endpoint(address)


def test_endpoint_accepts_loopback_and_normalizes_https():
    assert normalize_endpoint("http://[::1]:8765/") == "http://[::1]:8765"
    assert normalize_endpoint("https://EXAMPLE.COM:443/") == "https://example.com"


@contextmanager
def desktop(directory):
    binary = os.environ.get("KONGGU_CLOUD_TEST_WORKER")
    command = [binary] if binary else [sys.executable, "-m", "app.sidecar"]
    process = subprocess.Popen(command + ["--serve"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               encoding="utf-8", env={**os.environ, "KONGGU_APP_DATA_ROOT": str(directory)})
    scope = ""

    def request(command, payload=None, *, expect_error=False, with_scope=None):
        nonlocal scope
        identifier = str(uuid4())
        packet = {"id": identifier, "command": command, "scope": scope if with_scope is None else with_scope,
                  "payload": payload or {}}
        process.stdin.write(json.dumps(packet) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            message = json.loads(line)
            if message.get("event") == "sidecar.fatal":
                pytest.fail(message["message"])
            if message.get("id") != identifier:
                continue
            if expect_error:
                assert not message["ok"]
                return message["error"]
            assert message["ok"], message.get("error")
            result = message["data"]
            if command in {"cloud.login", "cloud.register", "cloud.logout"}:
                scope = result["scope"]
            return result
        pytest.fail("识别服务提前关闭")

    try:
        yield request
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


@pytest.mark.skipif(not os.environ.get("KONGGU_CLOUD_TEST_PYTHON"), reason="独立云服务测试环境未指定")
def test_two_desktop_makers_resume_edit_and_export(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([os.environ["KONGGU_CLOUD_TEST_PYTHON"], str(ROOT / "cloud_service/server.py"),
                               "--port", str(port)], env={**os.environ, "KONGGU_CLOUD_DATA": str(tmp_path / "server")})
    try:
        for _ in range(80):
            try:
                urlopen(endpoint + "/v1/batches", timeout=1).close()
            except HTTPError as exc:
                if exc.code == 401:
                    break
            except URLError:
                pass
            assert server.poll() is None
            time.sleep(.15)
        else:
            pytest.fail("本地账号服务没有就绪")

        credentials = {"endpoint": endpoint, "username": "demo_maker", "password": secrets.token_urlsafe(18)}
        with desktop(tmp_path / "one") as first, desktop(tmp_path / "two") as second:
            pair = [str(path) for path in _pair(tmp_path)]
            # Normal offline workflow first. Logging in must not implicitly claim it.
            guest_job = first("job.start", {"paths": pair, "workspace": True})["job_id"]
            for _ in range(600):
                job = first("job.get", {"job_id": guest_job})["job"]
                if job["status"] == "completed":
                    break
                assert job["status"] not in {"failed", "cancelled"}
                time.sleep(.2)
            else:
                pytest.fail("合成课表识别超时")
            auth = first("cloud.register", credentials)
            assert auth["scope"] and "token" not in auth
            assert not first("workspace.get")["files"]
            assert first("cloud.list")["batches"] == []
            copied = first("cloud.copy_guest", {"title": "合成排班"})
            first_job = first("cloud.open", {"id": copied["id"]})["job_id"]
            assert first("cloud.status")["batch"]["dirty"] is False

            second("cloud.login", credentials)
            assert second("cloud.list")["batches"] == [copied]
            second_job = second("cloud.open", {"id": copied["id"]})["job_id"]
            assert all(not f["path"] for f in second("workspace.get")["files"])
            review = second("review.get", {"job_id": second_job})
            second("review.apply", {"job_id": second_job, "block_id": review["blocks"][0]["block_id"],
                                    "field": "course", "new_value": "合成修订课程", "reason": "合成核验"})
            review = second("review.get", {"job_id": second_job})
            pending = next(item for item in review["verification"]["items"] if not item["confirmed"])
            assert "原件" in second("review.verify", {"job_id": second_job, "item_id": pending["item_id"],
                                                     "revision": review["revision"]}, expect_error=True)
            # Looking up results and editing a course require no re-import.
            # A new verification receipt still requires a matching local PDF.
            second("workspace.attach", {"job_id": second_job, "paths": pair, "remember": False})
            for item in review["verification"]["items"]:
                if not item["confirmed"]:
                    review = second("review.verify", {"job_id": second_job, "item_id": item["item_id"],
                                                      "revision": review["revision"]})
            assert second("cloud.status")["batch"]["dirty"]
            second("cloud.save", {"job_id": second_job, "id": copied["id"], "base_revision": 1, "title": "合成排班"})
            assert "更新" in first("cloud.save", {"job_id": first_job, "id": copied["id"], "base_revision": 1,
                                                "title": "旧设备的修改"}, expect_error=True)
            final_job = first("cloud.open", {"id": copied["id"]})["job_id"]
            excel = tmp_path / "云端恢复.xlsx"
            first("exports.excel", {"result_ref": final_job, "target_path": str(excel), "mode": "classic"})
            book = load_workbook(excel, read_only=True)
            try:
                assert any("合成修订课程" in str(cell) for sheet in book for row in sheet.values for cell in row)
            finally:
                book.close()
            first("cloud.logout")
            assert first("workspace.get")["job_id"] == guest_job
            other = first("cloud.register", {**credentials, "username": "another_maker"})
            assert other["scope"] != auth["scope"] and first("cloud.list")["batches"] == []
            assert not first("workspace.get")["files"]
            first("cloud.open", {"id": copied["id"]}, expect_error=True)
            first("workspace.get", with_scope=auth["scope"], expect_error=True)
            first("cloud.logout")
            first("cloud.login", credentials)
            assert first("cloud.status")["batch"]["revision"] == 2
    finally:
        server.terminate()
        server.wait(timeout=10)

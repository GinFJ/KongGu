"""Black-box tests against the shipping HTTP entry point and a disposable database."""
import concurrent.futures
from contextlib import closing
import datetime
import hashlib
import http.client
import importlib.util
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path


SERVER = Path(__file__).resolve().parent / "server.py"


@unittest.skipUnless(importlib.util.find_spec("django") and importlib.util.find_spec("waitress"),
                     "需使用 cloud_service/requirements.txt 的独立服务端环境")
class CloudHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.launch()
        cls.addClassCleanup(cls.stop)

    @classmethod
    def launch(cls):
        env = {**os.environ, "KONGGU_CLOUD_DATA": cls.directory.name, "KONGGU_CLOUD_HOST": ""}
        executable = sys.executable
        if os.name == "nt":
            # Own the actual server process. The Windows venv redirector launches a child
            # which can otherwise survive terminate() and keep the private test DB open.
            executable = sys._base_executable
            env["PYTHONPATH"] = sysconfig.get_path("purelib")
        cls.process = subprocess.Popen([executable, str(SERVER), "--port", str(cls.port)], env=env,
                                       stdout=subprocess.DEVNULL)
        until = time.monotonic() + 30
        while time.monotonic() < until:
            if cls.process.poll() is not None:
                raise RuntimeError("Service failed to start.")
            try:
                if cls.http("GET", "/v1/batches")[0] == 401:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        cls.stop()
        raise RuntimeError("Service did not become ready.")

    @classmethod
    def stop(cls):
        cls.process.terminate()
        cls.process.wait(timeout=15)

    @classmethod
    def http(cls, method, path, payload=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def account(self):
        user = {"username": "test-" + uuid.uuid4().hex, "password": secrets.token_urlsafe(24)}
        status, session = self.http("POST", "/v1/register", user)
        self.assertEqual(status, 201, session)
        self.assertEqual(uuid.UUID(session["account_id"]).version, 4)
        return user, session

    def test_accounts_restore_isolated_documents_and_revoke_tokens(self):
        user, first = self.account()
        _, second = self.account()
        path = "/v1/batches/" + str(uuid.uuid4())
        data = {"title": "合成课表", "base_revision": 0, "document": {"members": ["测试甲"]}}
        self.assertEqual(self.http("PUT", path, data, first["token"])[0], 201)
        self.assertEqual(self.http("GET", "/v1/batches", token=second["token"]), (200, {"batches": []}))
        self.assertEqual(self.http("GET", path, token=second["token"])[0], 404)
        self.assertEqual(self.http("PUT", path, {**data, "base_revision": 1}, second["token"])[0], 404)
        # The same client-generated UUID can exist independently in each account.
        self.assertEqual(self.http("PUT", path, {**data, "document": {}}, second["token"])[0], 201)
        self.assertEqual(self.http("POST", "/v1/logout", token=first["token"])[0], 200)
        self.assertEqual(self.http("GET", path, token=first["token"])[0], 401)
        status, logged_in = self.http("POST", "/v1/login", user)
        self.assertEqual(status, 200)
        self.assertEqual(logged_in["account_id"], first["account_id"])
        status, restored = self.http("GET", path, token=logged_in["token"])
        self.assertEqual((status, restored["document"], restored["revision"]), (200, data["document"], 1))
        index = self.http("GET", "/v1/batches", token=logged_in["token"])[1]["batches"]
        self.assertEqual(index, [{key: restored[key] for key in ("id", "title", "revision", "updated_at")}])
        self.assertEqual(self.http("GET", path, token=second["token"])[1]["document"], {})

    def test_conflicts_on_simultaneous_create_and_update(self):
        _, session = self.account()
        path = "/v1/batches/" + str(uuid.uuid4())
        for revision in (0, 1):
            barrier = threading.Barrier(2)
            def put(number):
                data = {"base_revision": revision, "title": "合成", "document": {"value": number}}
                barrier.wait(timeout=10)
                return self.http("PUT", path, data, session["token"])[0], number
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                responses = dict(pool.map(put, [1, 2]))
            expected = 201 if revision == 0 else 200
            self.assertEqual(set(responses), {expected, 409})
            result = self.http("GET", path, token=session["token"])[1]
            self.assertEqual((result["revision"], result["document"]["value"]), (revision + 1, responses[expected]))

    def test_durable_rows_and_expiring_hashed_sessions(self):
        user, session = self.account()
        path = "/v1/batches/" + str(uuid.uuid4())
        self.assertEqual(self.http("PUT", path, {"base_revision": 0, "title": "持久化", "document": {}},
                                   session["token"])[0], 201)
        self.stop()
        self.launch()
        self.assertEqual(self.http("GET", path, token=session["token"])[0], 200)
        status, fresh = self.http("POST", "/v1/login", user)
        self.assertEqual((status, fresh["account_id"]), (200, session["account_id"]))
        with closing(sqlite3.connect(Path(self.directory.name) / "konggu.sqlite3")) as db:
            password = db.execute("SELECT password FROM auth_user WHERE username=?", (user["username"],)).fetchone()[0]
            self.assertTrue(password.startswith("pbkdf2_sha256$"))
            digest = hashlib.sha256(session["token"].encode()).hexdigest()
            yesterday = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
            cursor = db.execute("UPDATE storage_accesstoken SET expires_at=? WHERE digest=?", (yesterday.isoformat(), digest))
            self.assertEqual(cursor.rowcount, 1)
            db.commit()
        self.assertEqual(self.http("GET", path, token=session["token"])[0], 401)

    def test_limits_and_authentication_errors(self):
        self.assertEqual(self.http("POST", "/v1/register", {"username": "example", "password": "123"})[0], 400)
        user, session = self.account()
        self.assertEqual(self.http("POST", "/v1/register", user)[0], 409)
        self.assertEqual(self.http("POST", "/v1/login", {**user, "password": "bad-password"})[0], 401)
        path = "/v1/batches/" + str(uuid.uuid4())
        self.assertEqual(self.http("PUT", path, {"title": "x", "base_revision": True, "document": {}},
                                   session["token"])[0], 400)
        # Document itself exceeds 64 MiB while the envelope is still within the HTTP limit.
        oversized = {"title": "x", "base_revision": 0, "document": {"x": "a" * (64 * 1024 * 1024)}}
        self.assertEqual(self.http("PUT", path, oversized, session["token"])[0], 413)
        self.assertEqual(self.http("GET", path, token=session["token"])[0], 404)
        self.assertEqual(self.http("POST", "/v1/login", {"username": "a" * 5000, "password": "x"})[0], 413)
        for _ in range(11):
            status, _ = self.http("POST", "/v1/login", {"username": "rate-limit-check", "password": "invalid"})
        self.assertEqual(status, 429)


if __name__ == "__main__":
    unittest.main()

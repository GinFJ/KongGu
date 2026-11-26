"""An authenticated maker's local workspace and its explicit cloud saves."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from uuid import UUID, uuid5, NAMESPACE_URL

from app.offline_resources import _purge_expired_processing_cache
from app.services import cloud_transport
from app.services.calendar_settings import apply_calendar_settings, load_calendar_settings
from app.services.job_service import JobCoordinator
from app.services.state_store import StateStore


_CACHE_DIRS = {
    "KONGGU_OCR_TEXT_CACHE": "pdf_text", "KONGGU_OCR_LAYOUT_CACHE": "pdf_layout",
    "KONGGU_PARSE_CACHE": "parsed_blocks", "KONGGU_RASTER_CLIP_CACHE": "pdf_clip",
}


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        raise ValueError("云批次编号无效。") from None


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise ValueError("请填写 1 到 100 字的批次名称。")
    return value.strip()


def _meta(value, expected=""):
    identifier = _uuid(value.get("id"))
    if (expected and identifier != expected or type(value.get("revision")) is not int or
            value["revision"] < 1 or not isinstance(value.get("updated_at"), str)):
        raise ValueError("云批次记录不完整，请刷新后重试。")
    return dict(id=identifier, revision=value["revision"], title=_title(value.get("title")),
                updated_at=value["updated_at"][:64])


def _local_digest(db, job_id):
    row = db.execute("SELECT result_json FROM jobs WHERE id=? AND status='completed'", (job_id,)).fetchone()
    if not row or not row[0]:
        return ""
    digest = hashlib.sha256(row[0].encode())
    for issue in db.execute("SELECT issue_id FROM issues WHERE job_id=? AND confirmed=1 ORDER BY issue_id", (job_id,)):
        digest.update(issue[0].encode())
    return digest.hexdigest()


def _remember_link(store, job_id, meta):
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO cloud_batches(job_id,metadata,digest) VALUES(?,?,?)",
                   (job_id, json.dumps(meta, ensure_ascii=False), _local_digest(db, job_id)))


class CloudWorkspace:
    def __init__(self, paths, coordinator):
        self.guest_paths = self.paths = paths
        self.guest = self.coordinator = coordinator
        self.guest_cache = {key: os.environ.get(key) for key in _CACHE_DIRS}
        self.endpoint = self.account_id = self.username = self.scope = self._token = ""

    def check_scope(self, expected):
        if expected != self.scope:
            raise ValueError("账号状态已变化，请重新登录或返回本机工作区。")

    def status(self):
        batch = None
        if self.scope:
            with self.coordinator.store.connect() as db:
                row = db.execute("SELECT job_id,metadata,digest FROM cloud_batches "
                                 "JOIN workspace USING(job_id) WHERE workspace.id=1").fetchone()
                if row:
                    batch = {**json.loads(row["metadata"]), "dirty": row["digest"] != _local_digest(db, row["job_id"])}
        return dict(scope=self.scope, account_id=self.account_id, username=self.username, endpoint=self.endpoint, batch=batch)

    def _activate(self, paths, coordinator, scope):
        from app.services.ai_review import clear_previews
        # One persistent worker owns the OCR process; the caller checks for an active job.
        clear_previews()
        self.guest.schedule_core.clear_page_image_cache()
        for key, subdirectory in _CACHE_DIRS.items():
            target = str(paths.cache_root / subdirectory) if scope else self.guest_cache[key]
            if target is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = target
        apply_calendar_settings(load_calendar_settings(paths))
        self.paths, self.coordinator, self.scope = paths, coordinator, scope

    def _login(self, action, data):
        endpoint = cloud_transport.normalize_endpoint(data.get("endpoint", ""))
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or not password:
            raise ValueError("请输入账号和密码。")
        auth = cloud_transport.call(endpoint, "POST", action, payload=dict(username=username.strip(), password=password))
        account_id = str(auth.get("account_id") or "")
        token, name = auth.get("token"), auth.get("username")
        if (not isinstance(name, str) or not name or not account_id or not isinstance(token, str)
                or not token.isascii() or not token.isprintable() or not token or len(token)>1024):
            raise ValueError("云服务未返回有效账号，请核对服务地址。")
        scope = hashlib.sha256((endpoint + "\n" + account_id).encode()).hexdigest()
        root = self.guest_paths.app_data_root / "accounts" / scope
        paths = replace(self.guest_paths, app_data_root=root, cache_root=root / "cache", logs_root=root / "logs")
        store = StateStore.from_resource_paths(paths)
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cloud_batches (job_id TEXT PRIMARY KEY REFERENCES jobs(id) "
                       "ON DELETE CASCADE, metadata TEXT NOT NULL, digest TEXT NOT NULL)")
        _purge_expired_processing_cache(paths)
        coordinator = JobCoordinator(self.guest.schedule_core, store, self.guest.event_sink)
        self._activate(paths, coordinator, scope)
        self.endpoint, self.account_id, self.username, self._token = endpoint, account_id, name, token
        return self.status()

    def _logout(self):
        if self._token:
            try:
                cloud_transport.call(self.endpoint, "POST", "logout", payload={}, token=self._token)
            except ValueError:
                # Offline sign-out still removes the only client copy of the bearer.
                pass
        self._activate(self.guest_paths, self.guest, "")
        self.endpoint = self.account_id = self.username = self._token = ""
        return self.status()

    def command(self, command, data):
        if command == "cloud.status":
            return self.status()
        if self.coordinator.has_active_job():
            raise ValueError("课表正在处理中，请等这一批处理完成后再操作账号或云批次。")
        if command in {"cloud.login", "cloud.register"}:
            return self._login(command.split(".")[1], data)
        if command == "cloud.logout":
            return self._logout()
        if not self._token:
            raise ValueError("请先登录账号。")
        if command == "cloud.list":
            value = cloud_transport.call(self.endpoint, "GET", "batches", token=self._token)
            rows = value.get("batches")
            if not isinstance(rows, list):
                raise ValueError("云服务未返回批次列表。")
            return {"batches": [_meta(row) for row in rows]}
        from app.services.cloud_batch import export_batch, import_batch
        if command == "cloud.open":
            identifier = _uuid(data.get("id"))
            reply = cloud_transport.call(self.endpoint, "GET", "batches/" + identifier, token=self._token)
            meta = _meta(reply, identifier)
            restored = import_batch(self.coordinator.store, reply.get("document"), self.paths)
            job_id = restored["job_id"]
            _remember_link(self.coordinator.store, job_id, meta)
            return {**meta, "job_id": job_id}
        if command not in {"cloud.save", "cloud.copy_guest"}:
            raise ValueError("不支持的云操作。")
        guest = command == "cloud.copy_guest"
        store = self.guest.store if guest else self.coordinator.store
        paths = self.guest_paths if guest else self.paths
        with store.connect() as db:
            current = db.execute("SELECT job_id FROM workspace WHERE id=1").fetchone()
            if guest and current is None:
                current = db.execute("SELECT id FROM jobs WHERE status='completed' ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
        if not current or not current[0] or (not guest and current[0] != data.get("job_id")):
            raise ValueError("请先生成课表，或重新打开需要保存的本机批次。")
        job_id = current[0]
        revision = data.get("base_revision", 0)
        if type(revision) is not int or revision < 0:
            raise ValueError("云批次版本无效。")
        identifier = (_uuid(data["id"]) if data.get("id") else
                      str(uuid5(NAMESPACE_URL, self.scope + "/" + ("guest/" if guest else "") + job_id)))
        document = export_batch(store, job_id, paths)
        title = _title(data.get("title"))
        payload = dict(title=title, base_revision=revision, document=document)
        route = "batches/" + identifier
        try:
            reply = cloud_transport.call(self.endpoint, "PUT", route, payload=payload, token=self._token)
        except cloud_transport.CloudError as exc:
            if exc.status != 409:
                raise
            # A lost response can follow a committed write. A retry of identical
            # data is safe; different content always requires user resolution.
            existing = cloud_transport.call(self.endpoint, "GET", route, token=self._token)
            if existing.get("title") != title or existing.get("document") != document:
                raise exc
            reply = existing
        meta = _meta(reply, identifier)
        if not guest:
            _remember_link(store, job_id, meta)
        return meta

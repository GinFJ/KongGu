"""SQLite persistence for long-running jobs, review decisions and audit events."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from app.offline_resources import ResourcePaths


SCHEMA_VERSION = 2
RETENTION_DAYS = 30


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StateStore:
    """Small connection-per-operation store safe for the sidecar worker thread."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._session_paths: dict[str, list[str]] = {}
        self.migrate()
        self.purge_expired()
        self.mark_running_jobs_interrupted()

    @classmethod
    def from_resource_paths(cls, paths: ResourcePaths) -> "StateStore":
        return cls(paths.app_data_root / "data" / "state.sqlite3")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    parser_signature TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    quality_state TEXT NOT NULL DEFAULT 'blocked',
                    current INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    active_source_id TEXT
                );
                CREATE TABLE IF NOT EXISTS job_files (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    source_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    UNIQUE(job_id, source_path)
                );
                CREATE TABLE IF NOT EXISTS issues (
                    issue_id TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    confirmed_at TEXT,
                    PRIMARY KEY(job_id, issue_id)
                );
                CREATE TABLE IF NOT EXISTS corrections (
                    id TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    block_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    original_json TEXT NOT NULL,
                    new_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    parser_signature TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS corrections_lookup
                    ON corrections(source_hash, parser_signature, stale);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            row = connection.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                version = int(row["version"])
                if version == 1:
                    self._migrate_issues_to_job_scoped_key(connection)
                    connection.execute("UPDATE schema_info SET version=?", (SCHEMA_VERSION,))
                elif version != SCHEMA_VERSION:
                    raise RuntimeError(f"不支持的 SQLite schema 版本：{row['version']}")

    @staticmethod
    def _migrate_issues_to_job_scoped_key(connection: sqlite3.Connection) -> None:
        """Make issue identity local to a submitted job while preserving history."""

        connection.execute("ALTER TABLE issues RENAME TO issues_v1")
        connection.execute(
            """
            CREATE TABLE issues (
                issue_id TEXT NOT NULL,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                confirmed_at TEXT,
                PRIMARY KEY(job_id, issue_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO issues(issue_id,job_id,payload_json,confirmed,confirmed_at)
            SELECT issue_id,job_id,payload_json,confirmed,confirmed_at
              FROM issues_v1
            """
        )
        connection.execute("DROP TABLE issues_v1")

    def mark_running_jobs_interrupted(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                   SET status='interrupted',
                       error='空谷上次关闭时课表仍在处理中，可重新处理失败文件。',
                       updated_at=?
                 WHERE status IN ('queued', 'running', 'cancelling')
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                UPDATE job_files
                   SET source_path='__cleared__:' || id
                 WHERE job_id IN (SELECT id FROM jobs WHERE status='interrupted')
                """
            )
            return int(cursor.rowcount)

    def create_job(self, request: dict[str, Any], parser_signature: str, paths: list[str]) -> str:
        job_id = str(uuid4())
        now = utc_now()
        self._session_paths[job_id] = list(paths)
        safe_request = {key: value for key, value in request.items() if key != "paths"}
        safe_request["file_count"] = len(paths)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    id,status,created_at,updated_at,parser_signature,request_json,current,total
                ) VALUES(?,?,?,?,?,?,0,?)
                """,
                (job_id, "queued", now, now, parser_signature, _json(safe_request), len(paths)),
            )
            for path in paths:
                file_name = Path(path).name
                connection.execute(
                    """
                    INSERT INTO job_files(
                        id,job_id,source_path,file_name,status,stage
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (str(uuid4()), job_id, path, file_name, "queued", "waiting"),
                )
        self.add_audit_event(job_id, "job.created", {"file_count": len(paths)})
        return job_id

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "result_json",
            "error",
            "quality_state",
            "current",
            "total",
            "active_source_id",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",
                (*values.values(), job_id),
            )

    def save_job_result(
        self,
        job_id: str,
        *,
        result_json: str,
        error: str,
        quality_state: str,
        current: int,
        total: int,
        issues: list[dict[str, Any]],
    ) -> None:
        """Atomically persist a completed result and its review issues."""

        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                   SET status='completed',
                       result_json=?,
                       error=?,
                       quality_state=?,
                       current=?,
                       total=?,
                       updated_at=?
                 WHERE id=?
                """,
                (
                    result_json,
                    error,
                    quality_state,
                    current,
                    total,
                    utc_now(),
                    job_id,
                ),
            )
            if not cursor.rowcount:
                raise ValueError("未找到要保存结果的任务。")
            self._replace_issues(connection, job_id, issues)

    def update_file(self, job_id: str, source_path: str, **fields: Any) -> None:
        allowed = {"kind", "source_hash", "status", "stage", "message"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE job_files SET {assignments} WHERE job_id=? AND source_path=?",
                (*values.values(), job_id, source_path),
            )

    def clear_job_source_paths(self, job_id: str) -> None:
        """Remove persisted source paths after a completed job is materialized."""

        with self.connect() as connection:
            connection.execute(
                "UPDATE job_files SET source_path='__cleared__:' || id WHERE job_id=?",
                (job_id,),
            )

    def source_paths_for_job(self, job_id: str) -> list[str]:
        """Return paths only to trusted in-process code that needs local review."""

        return list(self._session_paths.get(job_id, []))

    def source_path_map_for_job(self, job_id: str) -> dict[str, str]:
        """Map display filenames to current-session paths without exposing them in job responses."""

        return {Path(path).name: path for path in self._session_paths.get(job_id, [])}

    def get_job(
        self,
        job_id: str,
        *,
        include_result: bool = True,
        include_sensitive_paths: bool = False,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            payload = dict(row)
            payload["request"] = _loads(payload.pop("request_json"), {})
            result_json = payload.pop("result_json")
            payload["result"] = _loads(result_json, None) if include_result else None
            payload["files"] = []
            session_paths = self._session_paths.get(job_id, [])
            for index, item in enumerate(connection.execute(
                "SELECT * FROM job_files WHERE job_id=? ORDER BY rowid", (job_id,)
            ).fetchall()):
                file_payload = dict(item)
                if include_sensitive_paths:
                    file_payload["source_path"] = session_paths[index] if index < len(session_paths) else ""
                else:
                    file_payload.pop("source_path", None)
                payload["files"].append(file_payload)
            payload["issues"] = [
                {
                    **_loads(item["payload_json"], {}),
                    "confirmed": bool(item["confirmed"]),
                    "confirmed_at": item["confirmed_at"],
                }
                for item in connection.execute(
                    "SELECT * FROM issues WHERE job_id=? ORDER BY rowid", (job_id,)
                ).fetchall()
            ]
            return payload

    def failed_paths(self, job_id: str) -> list[str]:
        session_paths = self._session_paths.get(job_id, [])
        if not session_paths:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                    """
                    SELECT status FROM job_files
                     WHERE job_id=?
                     ORDER BY rowid
                    """,
                    (job_id,),
                ).fetchall()
        return [
            path
            for path, row in zip(session_paths, rows)
            if row["status"] in {"failed", "interrupted", "cancelled"}
        ]

    def clear_job_data(self, job_id: str) -> bool:
        """Delete one local processing record and its dependent review data."""

        with self.connect() as connection:
            hashes = [
                str(row["source_hash"])
                for row in connection.execute(
                    "SELECT source_hash FROM job_files WHERE job_id=? AND source_hash<>''", (job_id,)
                ).fetchall()
            ]
            if hashes:
                placeholders = ",".join("?" for _ in hashes)
                connection.execute(f"DELETE FROM corrections WHERE source_hash IN ({placeholders})", hashes)
            connection.execute("DELETE FROM audit_events WHERE job_id=?", (job_id,))
            cursor = connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self._session_paths.pop(job_id, None)
        self._checkpoint()
        return bool(cursor.rowcount)

    def clear_all_data(self) -> None:
        """Delete local processing results, corrections and audit history."""

        with self.connect() as connection:
            connection.execute("DELETE FROM corrections")
            connection.execute("DELETE FROM audit_events")
            connection.execute("DELETE FROM jobs")
        self._session_paths.clear()
        self._checkpoint()

    def purge_expired(self, retention_days: int = RETENTION_DAYS) -> int:
        """Apply bounded retention to old jobs and correction history."""

        cutoff = datetime.now(UTC) - timedelta(days=max(1, int(retention_days)))
        cutoff_text = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            old_jobs = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM jobs WHERE updated_at < ?", (cutoff_text,)
                ).fetchall()
            ]
            if old_jobs:
                placeholders = ",".join("?" for _ in old_jobs)
                connection.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", old_jobs)
                connection.execute(f"DELETE FROM audit_events WHERE job_id IN ({placeholders})", old_jobs)
            connection.execute("DELETE FROM corrections WHERE created_at < ?", (cutoff_text,))
        for job_id in old_jobs:
            self._session_paths.pop(job_id, None)
        if old_jobs:
            self._checkpoint()
        return len(old_jobs)

    def _checkpoint(self) -> None:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()

    def replace_issues(self, job_id: str, issues: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            self._replace_issues(connection, job_id, issues)

    @staticmethod
    def _replace_issues(
        connection: sqlite3.Connection,
        job_id: str,
        issues: list[dict[str, Any]],
    ) -> None:
        confirmed = {
            str(row["issue_id"]): (int(row["confirmed"]), row["confirmed_at"])
            for row in connection.execute(
                "SELECT issue_id,confirmed,confirmed_at FROM issues WHERE job_id=?", (job_id,)
            ).fetchall()
        }
        connection.execute("DELETE FROM issues WHERE job_id=?", (job_id,))
        for issue in issues:
            issue_id = str(issue.get("issue_id") or uuid4())
            prior = confirmed.get(issue_id, (0, None))
            connection.execute(
                """
                INSERT INTO issues(issue_id,job_id,payload_json,confirmed,confirmed_at)
                VALUES(?,?,?,?,?)
                """,
                (issue_id, job_id, _json(issue), prior[0], prior[1]),
            )

    def confirm_issue(self, job_id: str, issue_id: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE issues SET confirmed=1, confirmed_at=?
                 WHERE job_id=? AND issue_id=?
                """,
                (utc_now(), job_id, issue_id),
            )
            if not cursor.rowcount:
                raise ValueError("未找到要确认的问题。")
        self.add_audit_event(job_id, "review.confirmed", {"issue_id": issue_id})

    def add_correction(
        self,
        *,
        source_hash: str,
        block_id: str,
        field: str,
        original_value: Any,
        new_value: Any,
        reason: str,
        parser_signature: str,
        operator_id: str,
        job_id: str | None = None,
    ) -> str:
        correction_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO corrections(
                    id,source_hash,block_id,field,original_json,new_json,reason,
                    parser_signature,operator_id,created_at,stale
                ) VALUES(?,?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    correction_id,
                    source_hash,
                    block_id,
                    field,
                    _json(original_value),
                    _json(new_value),
                    reason,
                    parser_signature,
                    operator_id,
                    utc_now(),
                ),
            )
        self.add_audit_event(job_id, "review.corrected", {"correction_id": correction_id, "field": field})
        return correction_id

    def corrections_for_source(self, source_hash: str, parser_signature: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM corrections WHERE source_hash=?
                ORDER BY created_at,id
                """,
                (source_hash,),
            ).fetchall()
        corrections = []
        for row in rows:
            item = dict(row)
            item["original_value"] = _loads(item.pop("original_json"), None)
            item["new_value"] = _loads(item.pop("new_json"), None)
            item["stale"] = bool(item["stale"] or item["parser_signature"] != parser_signature)
            corrections.append(item)
        return corrections

    def mark_other_signatures_stale(self, source_hash: str, parser_signature: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE corrections SET stale=1
                 WHERE source_hash=? AND parser_signature<>?
                """,
                (source_hash, parser_signature),
            )
            return int(cursor.rowcount)

    def add_audit_event(self, job_id: str | None, event: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(job_id,event,payload_json,created_at)
                VALUES(?,?,?,?)
                """,
                (job_id, event, _json(payload), utc_now()),
            )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default

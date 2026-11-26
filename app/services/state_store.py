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


def _is_corrupt_database_error(exc: sqlite3.DatabaseError) -> bool:
    text = str(exc).lower()
    return "not a database" in text or "malformed" in text or "corrupt" in text


class StateStore:
    """Small connection-per-operation store safe for the sidecar worker thread."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._session_paths: dict[str, list[str]] = {}
        try:
            self.migrate()
        except (sqlite3.DatabaseError, RuntimeError) as exc:
            # 损坏的数据库或来自更新版本的 schema 不能让整个应用打不开：
            # 隔离旧文件后重建空库，旧数据仍可从 .corrupt 文件手工恢复。
            corrupt = isinstance(exc, sqlite3.DatabaseError) and _is_corrupt_database_error(exc)
            unsupported_schema = isinstance(exc, RuntimeError) and "schema" in str(exc).lower()
            if not (corrupt or unsupported_schema):
                raise
            self._quarantine_database()
            self.migrate()
        self.purge_expired()
        self.mark_running_jobs_interrupted()
        self._upgrade_date_review_results()

    def _quarantine_database(self) -> None:
        """Move an unusable database aside so a fresh one can be created."""

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            if self.path.exists():
                self.path.replace(target)
            for suffix in ("-wal", "-shm"):
                sidecar = self.path.with_name(self.path.name + suffix)
                if sidecar.exists():
                    sidecar.unlink()
        except OSError:
            # 隔离失败就让原异常继续上抛，不能假装已经恢复。
            raise

    @classmethod
    def from_resource_paths(cls, paths: ResourcePaths) -> "StateStore":
        return cls(paths.app_data_root / "data" / "state.sqlite3")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            # journal_mode is persistent in the database file and is set once in
            # migrate(); re-setting it on every poll connection costs ~2.9ms each.
            # 删除的行必须被覆写：隐私清理不能只把行从索引里摘掉，
            # 否则 sqlite 主文件里仍能扫出姓名、课程与路径。
            connection.execute("PRAGMA secure_delete = ON")
            yield connection
            connection.commit()
        finally:
            # PRAGMA 本身也可能在损坏库上抛错；连接必须始终关闭，
            # 否则后续隔离/重命名会因文件句柄占用失败。
            connection.close()

    def migrate(self) -> None:
        setup = sqlite3.connect(self.path, timeout=30)
        try:
            # WAL 是文件级持久设置，只在迁移时设置一次。
            setup.execute("PRAGMA journal_mode = WAL")
        finally:
            setup.close()
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace (
                    id INTEGER PRIMARY KEY CHECK(id=1), job_id TEXT NOT NULL,
                    selection TEXT NOT NULL, remember INTEGER NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_dates (
                    workspace_id INTEGER PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL
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
                CREATE TABLE IF NOT EXISTS local_batch_history (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                    keep_result INTEGER NOT NULL,
                    selection TEXT NOT NULL,
                    remember INTEGER NOT NULL,
                    paths_updated_at TEXT NOT NULL
                );
                -- Preserve the previous release's single remembered selection with its
                -- original expiry. Opening it explicitly will retain the batch result.
                INSERT OR IGNORE INTO local_batch_history
                    SELECT w.job_id,0,w.selection,w.remember,w.updated_at
                    FROM workspace w JOIN jobs j ON j.id=w.job_id;
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
        # Schema versioning runs in its own explicit transaction: a failed
        # migration must roll back instead of leaving a renamed/duplicated table
        # and an old version number that mark the database unopenable.
        self._migrate_schema_version()

    def _migrate_schema_version(self) -> None:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
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
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()

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
                 WHERE source_path <> '__cleared__:' || id
                   AND job_id IN (SELECT id FROM jobs WHERE status IN
                                   ('interrupted','completed','failed','cancelled'))
                """
            )
            return int(cursor.rowcount)

    def _upgrade_date_review_results(self) -> None:
        """Repair affected pre-fix results before an old approval can export."""
        with self.connect() as db:
            candidates = db.execute("""
                SELECT id,result_json FROM jobs WHERE status='completed' AND result_json IS NOT NULL
                  AND ((instr(result_json,'"date_review"')>0 AND instr(result_json,'"SCHEDULE_CONFLICT"')>0) OR id IN (
                    SELECT job_id FROM job_files WHERE source_hash<>''
                    GROUP BY job_id,source_hash HAVING count(DISTINCT file_name)>1
                  ))
            """).fetchall()
            for row in candidates:
                from .date_review import reconcile_saved_date_issues
                files = [dict(f) for f in db.execute(
                    "SELECT file_name,source_hash FROM job_files WHERE job_id=?", (row['id'],))]
                snapshot = json.loads(row['result_json'])
                if not reconcile_saved_date_issues(snapshot, files):
                    continue
                # Another worker may already have edited the job since SELECT.
                changed = db.execute("UPDATE jobs SET result_json=?,quality_state=? WHERE id=? AND result_json=?",
                    (_json(snapshot), snapshot['quality_state'], row['id'], row['result_json']))
                if changed.rowcount:
                    self._replace_issues(db, row['id'], snapshot['issues'])
                    for issue in snapshot['issues']:
                        if issue.get('confirmed'):
                            db.execute("UPDATE issues SET confirmed=1 WHERE job_id=? AND issue_id=?",
                                       (row['id'], issue['issue_id']))

    @staticmethod
    def _strip_request_paths(value: Any) -> Any:
        """Recursively drop source paths from a request before it is stored."""

        if isinstance(value, dict):
            return {
                key: StateStore._strip_request_paths(item)
                for key, item in value.items()
                if key not in {"paths", "source_path", "source_paths"}
            }
        if isinstance(value, list):
            return [StateStore._strip_request_paths(item) for item in value]
        return value

    def create_job(self, request: dict[str, Any], parser_signature: str, paths: list[str]) -> str:
        job_id = str(uuid4())
        now = utc_now()
        self._session_paths[job_id] = list(paths)
        # paths 不进持久化请求（顶层与嵌套 payload 都要剔除），只保留在内存会话里。
        safe_request = self._strip_request_paths(request)
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
            if request.get("workspace") is True:
                from .workspace_session import write_workspace
                write_workspace(connection, paths, request.get("remember_sources") is True, job_id)
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

    def update_job(
        self,
        job_id: str,
        *,
        connection: sqlite3.Connection | None = None,
        **fields: Any,
    ) -> None:
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
        statement = f"UPDATE jobs SET {assignments} WHERE id=?"
        params = (*values.values(), job_id)
        if connection is not None:
            connection.execute(statement, params)
            return
        with self.connect() as own:
            own.execute(statement, params)

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
        connection: sqlite3.Connection | None = None,
        completed: bool = True,
    ) -> None:
        """Persist result/issues together, optionally awaiting final review reuse."""

        if connection is not None:
            self._save_job_result_on(
                connection,
                job_id,
                result_json=result_json,
                error=error,
                quality_state=quality_state,
                current=current,
                total=total,
                issues=issues,
                completed=completed,
            )
            return
        with self.connect() as own:
            self._save_job_result_on(
                own,
                job_id,
                result_json=result_json,
                error=error,
                quality_state=quality_state,
                current=current,
                total=total,
                issues=issues,
                completed=completed,
            )

    def _save_job_result_on(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        result_json: str,
        error: str,
        quality_state: str,
        current: int,
        total: int,
        issues: list[dict[str, Any]],
        completed: bool,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE jobs
               SET status=?,
                   result_json=?,
                   error=?,
                   quality_state=?,
                   current=?,
                   total=?,
                   updated_at=?
             WHERE id=?
            """,
            (
                "completed" if completed else "running",
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

    def update_file(self, job_id: str, source_path: str, *, connection: sqlite3.Connection | None = None,
                    **fields: Any) -> None:
        allowed = {"kind", "source_hash", "status", "stage", "message"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        statement = f"UPDATE job_files SET {assignments} WHERE job_id=? AND source_path=?"
        parameters = (*values.values(), job_id, source_path)
        if connection is not None:
            connection.execute(statement, parameters)
        else:
            with self.connect() as own:
                own.execute(statement, parameters)

    def clear_job_source_paths(self, job_id: str) -> None:
        """Remove persisted source paths after a completed job is materialized."""

        with self.connect() as connection:
            connection.execute(
                "UPDATE job_files SET source_path='__cleared__:' || id WHERE job_id=?",
                (job_id,),
            )

    def bind_session_paths(self, job_id: str, paths: list[str]) -> None:
        self._session_paths[job_id] = list(paths)

    def source_paths_for_job(self, job_id: str) -> list[str]:
        """Return paths only to trusted in-process code that needs local review."""

        return list(self._session_paths.get(job_id, []))

    def source_path_map_for_job(self, job_id: str) -> dict[str, str]:
        """Map only unambiguous display filenames to current-session paths."""

        paths_by_name: dict[str, str] = {}
        ambiguous: set[str] = set()
        for path in self._session_paths.get(job_id, []):
            name = Path(path).name
            if name in paths_by_name and paths_by_name[name] != path:
                ambiguous.add(name)
            paths_by_name[name] = path
        return {name: path for name, path in paths_by_name.items() if name not in ambiguous}

    def get_job(
        self,
        job_id: str,
        *,
        include_result: bool = True,
        include_sensitive_paths: bool = False,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            columns = ("*" if include_result else
                       "id,status,created_at,updated_at,parser_signature,request_json,NULL AS result_json,"
                       "error,quality_state,current,total,active_source_id")
            row = connection.execute(f"SELECT {columns} FROM jobs WHERE id=?", (job_id,)).fetchone()
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
            connection.execute("DELETE FROM workspace WHERE job_id=?", (job_id,))
            cursor = connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self._session_paths.pop(job_id, None)
        self._checkpoint()
        return bool(cursor.rowcount)

    def clear_all_data(self) -> None:
        """Delete local processing results, corrections and audit history.

        Deleting rows is not enough for a privacy clear: the deleted bytes must be
        overwritten and the database rebuilt, or names, courses and paths stay
        recoverable in the file.
        """

        with self.connect() as connection:
            connection.execute("DELETE FROM workspace")
            connection.execute("DELETE FROM corrections")
            connection.execute("DELETE FROM audit_events")
            connection.execute("DELETE FROM jobs")
        self._session_paths.clear()
        # 行删除由 secure_delete 覆写；再截断 WAL 并重建主文件，清除旧页残留。
        self._checkpoint()
        self._secure_vacuum()
        self._checkpoint()

    def purge_expired(self, retention_days: int = RETENTION_DAYS) -> int:
        """Apply bounded retention to old jobs and correction history."""

        cutoff = datetime.now(UTC) - timedelta(days=max(1, int(retention_days)))
        cutoff_text = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            old_jobs = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT id FROM jobs WHERE updated_at < ? AND NOT
                       (status='completed' AND result_json IS NOT NULL AND EXISTS
                        (SELECT 1 FROM local_batch_history h WHERE h.job_id=jobs.id AND h.keep_result=1))""",
                    (cutoff_text,)
                ).fetchall()
            ]
            expired_workspace = connection.execute("DELETE FROM workspace WHERE updated_at < ?", (cutoff_text,)).rowcount
            expired_paths = connection.execute(
                """UPDATE local_batch_history SET selection='[]' WHERE selection<>'[]'
                   AND paths_updated_at < ?""", (cutoff_text,)).rowcount
            removed_corrections = 0
            if old_jobs:
                placeholders = ",".join("?" for _ in old_jobs)
                connection.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", old_jobs)
                connection.execute(f"DELETE FROM audit_events WHERE job_id IN ({placeholders})", old_jobs)
            cursor = connection.execute("""DELETE FROM corrections WHERE created_at < ? AND NOT EXISTS (
                SELECT 1 FROM local_batch_history h JOIN jobs j ON j.id=h.job_id
                JOIN job_files f ON f.job_id=j.id
                WHERE h.keep_result=1 AND j.status='completed' AND f.source_hash=corrections.source_hash
                    AND j.parser_signature=corrections.parser_signature
            )""", (cutoff_text,))
            removed_corrections = int(cursor.rowcount or 0)
        for job_id in old_jobs:
            self._session_paths.pop(job_id, None)
        if old_jobs or removed_corrections or expired_workspace or expired_paths:
            self._checkpoint()
            self._secure_vacuum()
            self._checkpoint()
        return len(old_jobs)

    def _checkpoint(self) -> None:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            connection.close()
        busy = int(row[0]) if row else 0
        if busy:
            raise RuntimeError("本机记录已删除，但数据库仍被其他窗口占用；请关闭其他空谷窗口后重试清理。")

    def _secure_vacuum(self) -> None:
        """Rebuild the database file so deleted pages cannot be recovered."""

        connection = sqlite3.connect(self.path, timeout=30)
        connection.isolation_level = None
        try:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("VACUUM")
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                "本机记录已删除，但数据库整理失败（可能仍被其他窗口占用）；请关闭其他空谷窗口后重试。"
            ) from exc
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

    def confirm_issue(
        self,
        job_id: str,
        issue_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        def apply(target: sqlite3.Connection) -> None:
            cursor = target.execute(
                """
                UPDATE issues SET confirmed=1, confirmed_at=?
                 WHERE job_id=? AND issue_id=?
                """,
                (utc_now(), job_id, issue_id),
            )
            if not cursor.rowcount:
                raise ValueError("未找到要确认的问题。")
            self.add_audit_event(job_id, "review.confirmed", {"issue_id": issue_id}, connection=target)

        if connection is not None:
            apply(connection)
            return
        with self.connect() as own:
            apply(own)

    def review_evidence(self, context_digest: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT job_id,payload_json FROM audit_events
                   WHERE event='review.evidence'
                     AND json_extract(payload_json, '$.context_digest')=? ORDER BY id""",
                (context_digest,),
            ).fetchall()
        return [{**_loads(row['payload_json'], {}), 'job_id': row['job_id']} for row in rows]

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
        connection: sqlite3.Connection | None = None,
    ) -> str:
        correction_id = str(uuid4())

        def apply(target: sqlite3.Connection) -> None:
            target.execute(
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
            self.add_audit_event(
                job_id,
                "review.corrected",
                {"correction_id": correction_id, "field": field},
                connection=target,
            )

        if connection is not None:
            apply(connection)
        else:
            with self.connect() as own:
                apply(own)
        return correction_id

    def corrections_for_source(self, source_hash: str, parser_signature: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM corrections WHERE source_hash=?
                ORDER BY created_at,rowid
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

    def add_audit_event(
        self,
        job_id: str | None,
        event: str,
        payload: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        statement = """
            INSERT INTO audit_events(job_id,event,payload_json,created_at)
            VALUES(?,?,?,?)
        """
        params = (job_id, event, _json(payload), utc_now())
        if connection is not None:
            connection.execute(statement, params)
            return
        with self.connect() as own:
            own.execute(statement, params)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default

"""In-memory task state for the Konggu local Web UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import threading
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
UPLOAD_ROOT = RUNTIME_ROOT / "uploads"
EXPORT_ROOT = RUNTIME_ROOT / "exports"

TASK_NOT_FOUND_MESSAGE = "任务不存在或已过期，请重新上传文件。"

TASK_STATUSES = {"IDLE", "UPLOADED", "RUNNING", "COMPLETED", "FAILED", "EXPORTED"}
LOG_LEVELS = {"INFO", "SUCCESS", "WARNING", "ERROR", "CACHE", "EXPORT"}


@dataclass(slots=True)
class UploadedFileRecord:
    """One PDF uploaded into a task workspace."""

    filename: str
    path: str
    size: int = 0
    source_type: str = "unknown"
    member_name: str = ""
    status: str = "uploaded"
    note: str = ""
    warning: str = ""

    @property
    def inferred_member(self) -> str:
        """Compatibility alias used by the first Web pass."""

        return self.member_name


@dataclass(slots=True)
class TaskLogRecord:
    """One structured task log entry."""

    time: str
    level: str
    message: str


@dataclass(slots=True)
class WebTask:
    """Mutable state retained for one browser workflow."""

    task_id: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    uploaded_files: list[UploadedFileRecord] = field(default_factory=list)
    pdf_sources: list[Any] = field(default_factory=list)
    status: str = "IDLE"
    progress: float = 0.0
    current_stage: str = ""
    current_file: str = ""
    logs: list[TaskLogRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    result: Any | None = None
    generation_result: Any | None = None
    preview_df: Any | None = None
    summary: dict[str, int] = field(default_factory=dict)
    export_path: str | None = None


class TaskManager:
    """A tiny thread-safe in-memory task store."""

    def __init__(self) -> None:
        self._tasks: dict[str, WebTask] = {}
        self._lock = threading.RLock()
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        EXPORT_ROOT.mkdir(parents=True, exist_ok=True)

    def create_task(self) -> WebTask:
        task_id = uuid4().hex
        task = WebTask(task_id=task_id, status="UPLOADED", progress=0.05, current_stage="文件已上传")
        with self._lock:
            self._tasks[task_id] = task
        (UPLOAD_ROOT / task_id).mkdir(parents=True, exist_ok=True)
        self.add_log(task_id, "INFO", "任务已创建。")
        return task

    def get_task(self, task_id: str) -> WebTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get(self, task_id: str) -> WebTask | None:
        return self.get_task(task_id)

    def require(self, task_id: str) -> WebTask:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def add_files(self, task_id: str, files: list[UploadedFileRecord], pdf_sources: list[Any]) -> WebTask:
        with self._lock:
            task = self._tasks[task_id]
            task.uploaded_files.extend(files)
            task.pdf_sources.extend(pdf_sources)
            task.current_file = files[0].filename if files else task.current_file
            task.updated_at = datetime.now()
            return task

    def set_status(self, task_id: str, status: str) -> WebTask:
        if status not in TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {status}")
        with self._lock:
            task = self._tasks[task_id]
            task.status = status
            task.updated_at = datetime.now()
            return task

    def set_progress(
        self,
        task_id: str,
        progress: float,
        stage: str | None = None,
        current_file: str | None = None,
    ) -> WebTask:
        with self._lock:
            task = self._tasks[task_id]
            task.progress = max(0.0, min(1.0, float(progress)))
            if stage is not None:
                task.current_stage = stage
            if current_file is not None:
                task.current_file = current_file
            task.updated_at = datetime.now()
            return task

    def add_log(self, task_id: str, level: str, message: str) -> None:
        normalized_level = level if level in LOG_LEVELS else "INFO"
        with self._lock:
            task = self._tasks[task_id]
            task.logs.append(
                TaskLogRecord(
                    time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    level=normalized_level,
                    message=message,
                )
            )
            task.updated_at = datetime.now()

    def set_result(
        self,
        task_id: str,
        *,
        result: Any,
        generation_result: Any,
        preview_df: Any,
        summary: dict[str, int],
    ) -> WebTask:
        with self._lock:
            task = self._tasks[task_id]
            task.result = result
            task.generation_result = generation_result
            task.preview_df = preview_df
            task.summary = summary
            task.status = "COMPLETED"
            task.progress = 1.0
            task.current_stage = "处理完成"
            task.current_file = "已完成"
            task.updated_at = datetime.now()
            return task

    def set_failed(self, task_id: str, error_message: str) -> WebTask:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "FAILED"
            task.progress = 0.0
            task.current_stage = "处理失败"
            task.current_file = "解析失败"
            task.errors.append(error_message)
            task.updated_at = datetime.now()
        self.add_log(task_id, "ERROR", error_message)
        return self._tasks[task_id]

    def update(self, task_id: str, **changes: Any) -> WebTask:
        with self._lock:
            task = self._tasks[task_id]
            for key, value in changes.items():
                setattr(task, key, value)
            task.updated_at = datetime.now()
            return task

    def clear_task(self, task_id: str, *, delete_files: bool = True) -> bool:
        with self._lock:
            existed = self._tasks.pop(task_id, None) is not None
        if delete_files:
            shutil.rmtree(UPLOAD_ROOT / task_id, ignore_errors=True)
            shutil.rmtree(EXPORT_ROOT / task_id, ignore_errors=True)
        return existed

    def clear(self, task_id: str) -> bool:
        return self.clear_task(task_id)

    def cleanup_expired_tasks(self, max_age_hours: int = 24) -> int:
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        with self._lock:
            expired = [task_id for task_id, task in self._tasks.items() if task.updated_at < cutoff]
        for task_id in expired:
            self.clear_task(task_id)
        return len(expired)


task_manager = TaskManager()


"""Single-active-job coordinator for the persistent sidecar process."""

from __future__ import annotations

import hashlib
from pathlib import Path
import threading
from typing import Any, Callable

from app.services.desktop_workflow import (
    parse_pdf_paths,
    save_workflow_result,
    serialize_workflow_result,
)
from app.services.review_service import apply_saved_corrections
from app.services.state_store import StateStore
from app.services.workflow_persistence import restore_workflow
from core.signature import build_parser_signature


EventSink = Callable[[dict[str, Any]], None]


class JobCoordinator:
    """Runs at most one parsing job and keeps OCR models in one process."""

    def __init__(self, schedule_core: Any, store: StateStore, event_sink: EventSink | None = None):
        self.schedule_core = schedule_core
        self.store = store
        self.event_sink = event_sink or (lambda event: None)
        self._lock = threading.Lock()
        self._active_job_id: str | None = None
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, payload: dict[str, Any], *, background: bool = True) -> dict[str, Any]:
        paths = [str(Path(path)) for path in payload.get("paths", []) if str(path).strip()]
        if not paths:
            raise ValueError("请先选择课表 PDF。")
        with self._lock:
            if self._active_job_id:
                raise ValueError(f"已有解析任务正在运行：{self._active_job_id}")
            signature = build_parser_signature().digest
            job_id = self.store.create_job(payload, signature, paths)
            self._active_job_id = job_id
            self._cancel_event = threading.Event()
        if background:
            self._thread = threading.Thread(
                target=self._run,
                args=(job_id, payload, paths),
                name=f"konggu-job-{job_id[:8]}",
                daemon=True,
            )
            self._thread.start()
        else:
            self._run(job_id, payload, paths)
        return {"ok": True, "job_id": job_id, "status": "queued"}

    def get(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id, include_result=True)
        if not job:
            raise ValueError("任务不存在。")
        payload = {key: value for key, value in job.items() if key != "result"}
        if job.get("result"):
            workflow = restore_workflow(job["result"], self.schedule_core)
            payload["output"] = serialize_workflow_result(workflow, job_id)
        return {"ok": True, "job": payload}

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if self._active_job_id != job_id:
                job = self.store.get_job(job_id, include_result=False)
                if not job:
                    raise ValueError("任务不存在。")
                return {"ok": True, "job_id": job_id, "status": job["status"]}
            self._cancel_event.set()
            self.store.update_job(job_id, status="cancelling", error="正在等待当前 OCR 页完成后取消。")
        self._emit(job_id, "", "cancelling", 0, 0, "已请求软取消；若 10 秒无响应，桌面端将重启 sidecar。")
        return {"ok": True, "job_id": job_id, "status": "cancelling"}

    def retry_failed(self, job_id: str, *, background: bool = True) -> dict[str, Any]:
        paths = self.store.failed_paths(job_id)
        if not paths:
            raise ValueError("该任务没有可重试的失败或中断文件。")
        original = self.store.get_job(job_id, include_result=False)
        payload = dict((original or {}).get("request") or {})
        payload["paths"] = paths
        payload["retry_of"] = job_id
        return self.start(payload, background=background)

    def _run(self, job_id: str, payload: dict[str, Any], paths: list[str]) -> None:
        self.store.update_job(job_id, status="running", error="", current=0, total=len(paths))
        file_rows = self.store.get_job(job_id, include_result=False)["files"]
        for index, file_row in enumerate(file_rows):
            self.store.update_file(
                job_id,
                file_row["source_path"],
                status="queued",
                stage="waiting",
                message="等待解析",
                source_hash=_file_hash(file_row["source_path"]),
            )
            self._emit(
                job_id,
                file_row["id"],
                "waiting",
                index,
                len(paths),
                f"{file_row['file_name']} 已进入顺序队列",
            )
        try:
            if self._cancel_event.is_set():
                raise InterruptedError("任务已取消。")

            def on_progress(stage: str, current: int, total: int, message: str) -> None:
                active = file_rows[min(current, max(0, len(file_rows) - 1))] if file_rows else None
                source_id = str(active["id"]) if active else ""
                if active:
                    self.store.update_file(
                        job_id,
                        active["source_path"],
                        status="running",
                        stage=stage,
                        message=message,
                    )
                self.store.update_job(
                    job_id,
                    current=current,
                    total=total,
                    active_source_id=source_id or None,
                )
                self._emit(job_id, source_id, stage, current, total, message)

            workflow = parse_pdf_paths(
                schedule_core=self.schedule_core,
                paths=paths,
                explicit_kind=payload.get("explicit_kind"),
                enforce_quality=True,
                progress=on_progress,
                cancelled=self._cancel_event.is_set,
            )
            if self._cancel_event.is_set():
                raise InterruptedError("任务已取消。")
            apply_saved_corrections(workflow, self.store, self.schedule_core)
            save_workflow_result(workflow, self.store, job_id=job_id, request=payload)
            state = workflow.process_result.quality_state
            for file_row in file_rows:
                record = next(
                    (
                        item
                        for item in workflow.process_result.file_records
                        if item.source.file_name == file_row["file_name"]
                    ),
                    None,
                )
                failed = bool(record and (record.error or record.block_count == 0))
                stage = "failed" if failed else ("review" if state != "accepted" else "completed")
                self.store.update_file(
                    job_id,
                    file_row["source_path"],
                    status="failed" if failed else "completed",
                    stage=stage,
                    message=record.display_result if record else "处理完成",
                )
            self.store.add_audit_event(job_id, "job.completed", {"quality_state": state})
            self._emit(
                job_id,
                "",
                "review" if state != "accepted" else "completed",
                len(paths),
                len(paths),
                "任务完成，存在待复核项" if state != "accepted" else "任务完成并通过质量门禁",
            )
        except InterruptedError as exc:
            self.store.update_job(job_id, status="cancelled", error=str(exc))
            for path in paths:
                self.store.update_file(
                    job_id,
                    path,
                    status="cancelled",
                    stage="cancelled",
                    message=str(exc),
                )
            self.store.add_audit_event(job_id, "job.cancelled", {"message": str(exc)})
            self._emit(job_id, "", "cancelled", 0, len(paths), str(exc))
        except Exception as exc:
            self.store.update_job(job_id, status="failed", error=str(exc))
            for path in paths:
                self.store.update_file(
                    job_id,
                    path,
                    status="failed",
                    stage="failed",
                    message=str(exc),
                )
            self.store.add_audit_event(job_id, "job.failed", {"error": str(exc)})
            self._emit(job_id, "", "failed", 0, len(paths), str(exc))
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def _emit(
        self,
        job_id: str,
        source_id: str,
        stage: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        self.event_sink(
            {
                "type": "event",
                "event": "job.progress",
                "job_id": job_id,
                "source_id": source_id,
                "stage": stage,
                "current": int(current),
                "total": int(total),
                "message": message,
            }
        )


def _file_hash(path: str) -> str:
    target = Path(path)
    if not target.exists():
        return ""
    hasher = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

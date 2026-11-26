"""Command-line sidecar used by the Tauri desktop app."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import load_schedule_core
from app.privacy import safe_error_message
from app.offline_resources import (
    clear_processing_cache, configure_offline_environment, processing_cache_usage, repair_resources, resource_status,
)
from app.services.calendar_settings import apply_calendar_settings, load_calendar_settings, save_calendar_settings
from app.services.desktop_workflow import (
    MAX_INPUT_FILES,
    export_excel,
    load_workflow_result,
)
from app.services.job_service import JobCoordinator
from app.services.review_service import apply_correction, confirm_issue, get_review_payload
from app.services.state_store import StateStore
from core.models import PdfSource
from core.pdf_inspection import inspect_pdf_sources


MAX_REQUEST_LINE_BYTES = 2_000_000


def _configure_stdio() -> None:
    """Keep sidecar protocol and diagnostics decodable by the Tauri shell plugin."""

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Test doubles and already-closed streams may not support reconfiguration.
            continue


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Konggu desktop sidecar.")
    parser.add_argument("--request", type=Path, help="Path to a JSON request file.")
    parser.add_argument("--request-json", help="Inline JSON request.")
    parser.add_argument("--serve", action="store_true", help="Run persistent NDJSON server mode.")
    args = parser.parse_args(argv)

    if args.serve:
        return serve()
    try:
        request = _load_request(args)
        response = dispatch(request)
    except Exception as exc:
        response = {
            "ok": False,
            "error": safe_error_message(exc),
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0 if response.get("ok") else 1


def dispatch(
    request: dict[str, Any],
    coordinator: JobCoordinator | None = None,
    *,
    account_paths=None,
) -> dict[str, Any]:
    nested_payload = request.get("payload")
    if isinstance(nested_payload, dict):
        request = {**request, **nested_payload}
    command = str(request.get("command") or "").strip()
    # 数量上限必须在写库之前检查：否则超限请求会先落 job_files 再被拒绝。
    if command == "job.start":
        started_paths = [
            str(item) for item in (request.get("paths") or []) if str(item).strip()
        ]
        if not started_paths:
            raise ValueError("请先选择课表 PDF。")
        if len(started_paths) > MAX_INPUT_FILES:
            raise ValueError(f"一次最多处理 {MAX_INPUT_FILES} 份 PDF。")
    paths = account_paths or configure_offline_environment()

    if command == "resources.status":
        return resource_status(check_runtime=True)
    if command == "resources.repair":
        return repair_resources()
    if command in {"data.usage", "data.clear_cache", "data.clear"}:
        store = coordinator.store if coordinator is not None else StateStore.from_resource_paths(paths)
        # Only the default offline profile may own the old unscoped processing cache.
        legacy_cache = account_paths is None and not os.environ.get("KONGGU_APP_DATA_ROOT")
        if command == "data.usage":
            from app.services.workspace_session import count_history
            count = count_history(store)
            database_bytes = 0
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    database_bytes += store.path.with_name(store.path.name + suffix).stat().st_size
                except FileNotFoundError:
                    pass
            return {"ok": True, "batch_count": count, "history_bytes": database_bytes,
                    **processing_cache_usage(paths, include_legacy=legacy_cache)}
        if coordinator is not None and coordinator.has_active_job():
            raise ValueError("课表正在处理中，请先停止处理，再清理本机记录。")
        # 先清缓存再清数据库：缓存被占用时中止，数据库还在，用户重试即可；
        # 反过来会出现“数据库已清但接口报错”的不可解释状态。
        cleared = clear_processing_cache(paths, include_legacy=legacy_cache)
        if command == "data.clear_cache":
            return {"ok": True, "cleared_cache": cleared}
        store.clear_all_data()
        from app.services.ai_review import clear_previews
        clear_previews()
        return {"ok": True, "cleared_cache": cleared}
    if command == "settings.calendar.get":
        return {"ok": True, "settings": load_calendar_settings(paths)}
    if command == "settings.calendar.save":
        settings = save_calendar_settings(paths, dict(request.get("settings") or {}))
        return {"ok": True, "settings": settings}
    if command == "pdf.inspect":
        source_paths = [Path(str(item)).resolve() for item in (request.get("paths") or []) if str(item).strip()]
        if not source_paths:
            raise ValueError("请提供需要检查的 PDF 路径。")
        invalid = [path.name for path in source_paths if path.suffix.lower() != ".pdf" or not path.is_file()]
        if invalid:
            raise ValueError("PDF 路径无效：" + "、".join(invalid))
        sources = [PdfSource(path.name, "中方", str(path)) for path in source_paths]
        return {"ok": True, "inspections": inspect_pdf_sources(sources)}

    apply_calendar_settings(load_calendar_settings(paths))
    ephemeral = coordinator is None
    if coordinator is None:
        schedule_core = load_schedule_core()
        store = StateStore.from_resource_paths(paths)
        coordinator = JobCoordinator(schedule_core, store)
    else:
        schedule_core = coordinator.schedule_core
        store = coordinator.store

    if command.startswith("workspace.") or command == "review.source_pdf":
        from app.services import workspace_session
        if command == "workspace.history":
            return workspace_session.list_history(store)
        if command == "workspace.open":
            if coordinator.has_active_job():
                raise ValueError("这批课表正在处理中，请等处理完成后再打开历史课表。")
            return workspace_session.open_history(store, str(request.get("job_id") or ""), paths)
        if command == "workspace.get":
            return workspace_session.load_workspace(store)
        if command == "workspace.save":
            return workspace_session.save_draft(store, request.get("files"), request.get("remember") is True,
                                                request.get("start_date"), request.get("end_date"))
        if command == "workspace.attach":
            return workspace_session.attach_originals(store, str(request.get("job_id") or ""),
                                                       request.get("paths"), request.get("remember") is True)
        if command == "review.source_pdf":
            return workspace_session.read_bound_pdf(store, str(request.get("job_id") or ""),
                                                    str(request.get("source_hash") or ""), str(request.get("file_name") or ""))

    if command == "schedules.parse":
        started = coordinator.start(request, background=False)
        response = coordinator.get(started["job_id"])
        job = response["job"]
        if job["status"] != "completed":
            raise ValueError(job.get("error") or "解析任务未完成。")
        return job["output"]

    if command == "job.start":
        started = coordinator.start(request.get("payload") or request, background=not ephemeral)
        if ephemeral:
            return coordinator.get(started["job_id"])
        return started
    if command == "job.get":
        return coordinator.get(str(request.get("job_id") or ""))
    if command == "job.cancel":
        return coordinator.cancel(str(request.get("job_id") or ""))
    if command == "job.retry_failed":
        return coordinator.retry_failed(
            str(request.get("job_id") or ""),
            background=not ephemeral,
        )

    if command == "review.get":
        return get_review_payload(store, str(request.get("job_id") or ""))
    if command in {"review.slot", "review.member", "review.flag"}:
        from app.services.date_review import update_date_review
        return update_date_review(store, schedule_core, str(request.get("job_id") or ""), request, command.split(".")[1])
    if command == "review.verify":
        from app.services.course_verification import verify_course
        return verify_course(store, str(request.get("job_id") or ""), str(request.get("item_id") or ""),
                             str(request.get("revision") or ""), compact=request.get("compact") is True)
    if command in {"ai.prepare", "ai.run", "ai.adopt"}:
        from app.services import ai_review
        if command == "ai.prepare":
            return ai_review.prepare(store, str(request.get('job_id') or ''), str(request.get('source_hash') or ''), request.get('page', 0))
        if command == "ai.run":
            return ai_review.run(store, request)
        return ai_review.adopt(store, schedule_core, request)
    if command == "review.apply":
        return apply_correction(
            store=store,
            schedule_core=schedule_core,
            job_id=str(request.get("job_id") or ""),
            block_id=str(request.get("block_id") or ""),
            field=str(request.get("field") or ""),
            new_value=request.get("new_value"),
            reason=str(request.get("reason") or ""),
            operator_id=str(request.get("operator_id") or "本机用户"),
        )
    if command == "review.mutate":
        from app.services.review_operations import mutate_courses
        return mutate_courses(store=store, schedule_core=schedule_core,
                              job_id=str(request.get("job_id") or ""), request=request)
    if command == "review.confirm":
        return confirm_issue(
            store,
            str(request.get("job_id") or ""),
            str(request.get("issue_id") or ""),
            reason=str(request.get("reason") or "已对照原文核对"),
            operator_id=str(request.get("operator_id") or "本机用户"),
        )

    if command == "exports.excel":
        result_ref = str(request.get("result_ref") or "")
        target_path = str(request.get("target_path") or "")
        mode = str(request.get("mode") or "classic")
        if not result_ref:
            raise ValueError("缺少解析结果引用，请先生成空课表。")
        if not target_path:
            raise ValueError("缺少导出路径。")
        workflow_result = load_workflow_result(result_ref, store, schedule_core)
        exported = export_excel(
            schedule_core=schedule_core,
            workflow_result=workflow_result,
            target_path=target_path,
            mode=mode,
            export_week_count=request.get("export_week_count"),
            department=request.get("department", ""),
            week=request.get("week"),
        )
        return {"ok": True, "path": str(exported), "mode": mode}

    raise ValueError(f"Unsupported sidecar command: {command}")


_WORKER_LOCK_HANDLE: Any = None


def _acquire_worker_slot(paths: Any) -> bool:
    """Return True when this worker owns the per-user OCR worker slot.

    A page reload (Ctrl+R) or a second app instance can spawn a second worker.
    The second worker's StateStore construction marks the first worker's running
    jobs as interrupted and drops their paths, so only one worker may run.

    Uses an OS-level file lock: the handle is kept for the process lifetime and
    the OS releases it when the process exits (even after a crash). Do not probe
    liveness with ``os.kill(pid, 0)`` on Windows: that calls TerminateProcess.
    """

    global _WORKER_LOCK_HANDLE

    lock_path = paths.app_data_root / "worker.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError:
        # 锁文件不可写时不要阻止启动（只读环境仍应可用），退化为无保护。
        return True
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _WORKER_LOCK_HANDLE = handle
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return True


def serve() -> int:
    """Serve one persistent NDJSON connection over stdin/stdout."""

    _configure_stdio()
    # OCR libraries redirect process-global stdout in the parsing thread.
    # Keep the protocol on its original pipe so concurrent replies cannot vanish.
    protocol_stdout = sys.stdout
    output_lock = threading.Lock()

    def write_message(message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with output_lock:
            protocol_stdout.write(encoded + "\n")
            protocol_stdout.flush()

    try:
        paths = configure_offline_environment()
        if not _acquire_worker_slot(paths):
            write_message(
                {
                    "type": "event",
                    "event": "sidecar.fatal",
                    "job_id": "",
                    "source_id": "",
                    "stage": "failed",
                    "current": 0,
                    "total": 0,
                    "message": "已有一个空谷识别进程在运行，请关闭其他空谷窗口后重试。",
                }
            )
            return 1
        apply_calendar_settings(load_calendar_settings(paths))
        schedule_core = load_schedule_core()
        store = StateStore.from_resource_paths(paths)
        coordinator = JobCoordinator(schedule_core, store, event_sink=write_message)
        from app.services.cloud_workspace import CloudWorkspace
        cloud = CloudWorkspace(paths, coordinator)
    except Exception as exc:
        write_message(
            {
                "type": "event",
                "event": "sidecar.fatal",
                "job_id": "",
                "source_id": "",
                "stage": "failed",
                "current": 0,
                "total": 0,
                "message": safe_error_message(exc),
            }
        )
        return 1

    for raw_line in sys.stdin:
        if len(raw_line) > MAX_REQUEST_LINE_BYTES:
            write_message(
                {
                    "id": "",
                    "type": "response",
                    "ok": False,
                    "error": "请求内容过大，已拒绝处理。",
                }
            )
            continue
        line = raw_line.lstrip("\ufeff").strip()
        if not line:
            continue
        request_id = ""
        try:
            request = json.loads(line)
            request_id = str(request.get("id") or "")
            command = request.get("command", "")
            # A restarted worker always begins as a guest. Never replay an old
            # account's file operation against that guest workspace.
            if command not in {"cloud.login", "cloud.register", "cloud.logout"} or cloud.scope:
                cloud.check_scope(request.get("scope", ""))
            if command.startswith("cloud."):
                result = cloud.command(command, request.get("payload") or {})
            elif cloud.scope:
                result = dispatch(request, cloud.coordinator, account_paths=cloud.paths)
            else:
                result = dispatch(request, coordinator)
            ok = bool(result.get("ok", True))
            if ok:
                write_message(
                    {
                        "id": request_id,
                        "type": "response",
                        "ok": True,
                        "data": result,
                    }
                )
            else:
                write_message(
                    {
                        "id": request_id,
                        "type": "response",
                        "ok": False,
                        "error": safe_error_message(result.get("error"), "本地识别功能执行失败。"),
                    }
                )
        except Exception as exc:
            write_message(
                {
                    "id": request_id,
                    "type": "response",
                    "ok": False,
                    "error": safe_error_message(exc),
                }
            )
    return 0


def _load_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.request:
        return json.loads(args.request.read_text(encoding="utf-8-sig"))
    if args.request_json:
        return json.loads(args.request_json.lstrip("\ufeff"))
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return json.loads(data.lstrip("\ufeff"))
    raise ValueError("没有收到可执行的处理请求。")


if __name__ == "__main__":
    raise SystemExit(main())

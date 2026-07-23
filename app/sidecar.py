"""Command-line sidecar used by the Tauri desktop app."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import load_schedule_core
from app.offline_resources import configure_offline_environment, repair_resources, resource_status
from app.services.calendar_settings import apply_calendar_settings, load_calendar_settings, save_calendar_settings
from app.services.desktop_workflow import (
    export_excel,
    load_workflow_result,
)
from app.services.job_service import JobCoordinator
from app.services.review_service import apply_correction, confirm_issue, get_review_payload
from app.services.state_store import StateStore


def main(argv: list[str] | None = None) -> int:
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
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0 if response.get("ok") else 1


def dispatch(
    request: dict[str, Any],
    coordinator: JobCoordinator | None = None,
) -> dict[str, Any]:
    nested_payload = request.get("payload")
    if isinstance(nested_payload, dict):
        request = {**request, **nested_payload}
    command = str(request.get("command") or "").strip()
    paths = configure_offline_environment()

    if command == "resources.status":
        return resource_status(check_runtime=True)
    if command == "resources.repair":
        return repair_resources()
    if command == "settings.calendar.get":
        return {"ok": True, "settings": load_calendar_settings(paths)}
    if command == "settings.calendar.save":
        settings = save_calendar_settings(paths, dict(request.get("settings") or {}))
        return {"ok": True, "settings": settings}

    apply_calendar_settings(load_calendar_settings(paths))
    ephemeral = coordinator is None
    if coordinator is None:
        schedule_core = load_schedule_core()
        store = StateStore.from_resource_paths(paths)
        coordinator = JobCoordinator(schedule_core, store)
    else:
        schedule_core = coordinator.schedule_core
        store = coordinator.store

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
    if command == "review.confirm":
        return confirm_issue(
            store,
            str(request.get("job_id") or ""),
            str(request.get("issue_id") or ""),
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
        )
        return {"ok": True, "path": str(exported), "mode": mode}

    raise ValueError(f"Unsupported sidecar command: {command}")


def serve() -> int:
    """Serve one persistent NDJSON connection over stdin/stdout."""

    output_lock = threading.Lock()

    def write_message(message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with output_lock:
            sys.stdout.write(encoded + "\n")
            sys.stdout.flush()

    try:
        paths = configure_offline_environment()
        apply_calendar_settings(load_calendar_settings(paths))
        schedule_core = load_schedule_core()
        store = StateStore.from_resource_paths(paths)
        coordinator = JobCoordinator(schedule_core, store, event_sink=write_message)
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
                "message": str(exc),
            }
        )
        return 1

    for raw_line in sys.stdin:
        line = raw_line.lstrip("\ufeff").strip()
        if not line:
            continue
        request_id = ""
        try:
            request = json.loads(line)
            request_id = str(request.get("id") or "")
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
                        "error": str(result.get("error") or "sidecar 执行失败。"),
                    }
                )
        except Exception as exc:
            write_message(
                {
                    "id": request_id,
                    "type": "response",
                    "ok": False,
                    "error": str(exc),
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
    raise ValueError("缺少 sidecar 请求。")


if __name__ == "__main__":
    raise SystemExit(main())

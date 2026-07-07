"""Command-line sidecar used by the Tauri desktop app."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from app.bootstrap import load_schedule_core
from app.offline_resources import configure_offline_environment, repair_resources, resource_status
from app.services.calendar_settings import apply_calendar_settings, load_calendar_settings, save_calendar_settings
from app.services.desktop_workflow import (
    export_excel,
    load_workflow_result,
    parse_pdf_paths,
    save_workflow_result,
    serialize_workflow_result,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Konggu desktop sidecar.")
    parser.add_argument("--request", type=Path, help="Path to a JSON request file.")
    parser.add_argument("--request-json", help="Inline JSON request.")
    args = parser.parse_args(argv)

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


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    command = str(request.get("command") or "").strip()
    paths = configure_offline_environment()

    if command == "resources.status":
        return resource_status()
    if command == "resources.repair":
        return repair_resources()
    if command == "settings.calendar.get":
        return {"ok": True, "settings": load_calendar_settings(paths)}
    if command == "settings.calendar.save":
        settings = save_calendar_settings(paths, dict(request.get("settings") or {}))
        return {"ok": True, "settings": settings}

    apply_calendar_settings(load_calendar_settings(paths))
    schedule_core = load_schedule_core()
    if command == "schedules.parse":
        workflow_result = parse_pdf_paths(
            schedule_core=schedule_core,
            paths=[str(path) for path in request.get("paths", [])],
            explicit_kind=request.get("explicit_kind"),
        )
        result_ref = save_workflow_result(workflow_result, paths.cache_root)
        return serialize_workflow_result(workflow_result, str(result_ref))

    if command == "exports.excel":
        result_ref = str(request.get("result_ref") or "")
        target_path = str(request.get("target_path") or "")
        mode = str(request.get("mode") or "classic")
        if not result_ref:
            raise ValueError("缺少解析结果引用，请先生成空课表。")
        if not target_path:
            raise ValueError("缺少导出路径。")
        workflow_result = load_workflow_result(result_ref)
        exported = export_excel(
            schedule_core=schedule_core,
            workflow_result=workflow_result,
            target_path=target_path,
            mode=mode,
            export_week_count=request.get("export_week_count"),
        )
        return {"ok": True, "path": str(exported), "mode": mode}

    raise ValueError(f"Unsupported sidecar command: {command}")


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

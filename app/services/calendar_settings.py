"""Persisted semester calendar settings for desktop runs."""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from typing import Any

from app.offline_resources import ResourcePaths
from core.signature import DEFAULT_SEMESTER_START_DATE, DEFAULT_TEACHING_WEEKS


def settings_path(paths: ResourcePaths) -> Path:
    return paths.app_data_root / "settings" / "calendar.json"


def load_calendar_settings(paths: ResourcePaths) -> dict[str, Any]:
    defaults = {
        "semester_start_date": DEFAULT_SEMESTER_START_DATE,
        "teaching_weeks": DEFAULT_TEACHING_WEEKS,
    }
    path = settings_path(paths)
    if not path.exists():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults
    try:
        return normalize_calendar_settings(payload)
    except ValueError:
        return defaults


def save_calendar_settings(paths: ResourcePaths, payload: dict[str, Any]) -> dict[str, Any]:
    settings = normalize_calendar_settings(payload)
    path = settings_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 原子替换：写入中断或磁盘满时不能留下半截 JSON 让设置静默回退默认值。
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    apply_calendar_settings(settings)
    return settings


def normalize_calendar_settings(payload: dict[str, Any]) -> dict[str, Any]:
    start_text = str(payload.get("semester_start_date") or "").strip()
    try:
        start = date.fromisoformat(start_text)
    except Exception as exc:
        raise ValueError("学期第 1 周周一日期格式应为 YYYY-MM-DD。") from exc
    if start.weekday() != 0:
        weekday_label = "一二三四五六日"[start.weekday()]
        raise ValueError(
            f"学期第 1 周日期必须是周一（{start_text} 是周{weekday_label}），请选择该周的周一。"
        )

    try:
        weeks = int(payload.get("teaching_weeks"))
    except Exception as exc:
        raise ValueError("教学周数应为整数。") from exc
    if not 1 <= weeks <= 30:
        raise ValueError("教学周数应在 1 到 30 周之间。")

    return {
        "semester_start_date": start.isoformat(),
        "teaching_weeks": weeks,
    }


def apply_calendar_settings(settings: dict[str, Any]) -> None:
    normalized = normalize_calendar_settings(settings)
    os.environ["KONGGU_SEMESTER_START_DATE"] = normalized["semester_start_date"]
    os.environ["KONGGU_TEACHING_WEEKS"] = str(normalized["teaching_weeks"])


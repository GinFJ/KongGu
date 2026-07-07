"""Application bootstrap helpers for the Konggu desktop app."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import logging
import os
import sys
from pathlib import Path

import sitecustomize  # noqa: F401  # Keep local/user site-package ordering stable before pandas imports.

from app.offline_resources import configure_offline_environment


def resource_path(name: str) -> Path:
    """Return a bundled resource path, or the project-root path in development."""

    configured = os.environ.get("KONGGU_RESOURCE_ROOT")
    if configured:
        candidate = Path(configured) / name
        if candidate.exists():
            return candidate
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parents[1] / name


def load_reference_library_config() -> dict:
    """Load the configured local schedule reference library metadata."""

    config_path = resource_path("config") / "reference_library.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def configure_runtime_environment() -> None:
    """Keep third-party caches inside the project or bundle directory."""

    resource_paths = configure_offline_environment()
    cache_root = resource_paths.cache_root
    cache_paths = {
        "MPLCONFIGDIR": cache_root / "matplotlib",
        "PADDLE_PDX_CACHE_HOME": resource_paths.ocr_models_root.parent,
        "PADDLE_HOME": cache_root / "paddle",
        "PADDLEOCR_HOME": cache_root / "paddleocr",
        "KONGGU_OCR_TEXT_CACHE": cache_root / "pdf_text",
        "KONGGU_OCR_LAYOUT_CACHE": cache_root / "pdf_layout",
        "KONGGU_PARSE_CACHE": cache_root / "parsed_blocks",
        "KONGGU_OCR_MODEL_DIR": resource_paths.ocr_models_root,
    }
    for env_name, path in cache_paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(env_name, str(path))
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.pop("KONGGU_ALLOW_OCR_MODEL_DOWNLOAD", None)


def load_schedule_core():
    """Load the schedule parsing core used by the desktop GUI."""

    configure_runtime_environment()
    os.environ.setdefault("STREAMLIT_GLOBAL_SUPPRESS_DEPRECATION_WARNINGS", "true")
    for logger_name in (
        "streamlit",
        "streamlit.runtime",
        "streamlit.runtime.caching",
        "streamlit.runtime.scriptrunner_utils",
    ):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)

    try:
        from core import schedule_core

        return schedule_core
    except Exception as exc:
        source_error = exc

    candidates = [
        resource_path("_recovered_core") / "app.pyc",
        Path.cwd() / "_recovered_core" / "app.pyc",
    ]

    for candidate in candidates:
        if not candidate.exists():
            continue

        spec = importlib.util.spec_from_file_location("schedule_app_core", candidate)
        if spec is None or spec.loader is None:
            continue

        module = importlib.util.module_from_spec(spec)
        sys.modules["schedule_app_core"] = module
        with contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
        return module

    raise RuntimeError("源码版课表核心加载失败，请检查 core/schedule_core.py。") from source_error

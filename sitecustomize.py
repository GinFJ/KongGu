"""Project-local import path repair for the Konggu desktop app.

The user's Anaconda Python loads the per-user site-packages directory before
Anaconda's own site-packages. That allowed a user-installed NumPy 2.x to shadow
the Anaconda NumPy 1.x build required by pandas/pyarrow. We keep the user site
available for OCR packages, but move the Anaconda site-packages path ahead of it
so binary packages come from one compatible stack.
"""

from __future__ import annotations

import site
import sys
import os
from pathlib import Path


def _normalize(path: str) -> str:
    return str(Path(path).resolve()).casefold()


def _move_before(target: str, before: str) -> None:
    target_norm = _normalize(target)
    before_norm = _normalize(before)
    target_index = next((i for i, item in enumerate(sys.path) if _normalize(item) == target_norm), None)
    before_index = next((i for i, item in enumerate(sys.path) if _normalize(item) == before_norm), None)
    if target_index is None or before_index is None or target_index < before_index:
        return

    value = sys.path.pop(target_index)
    before_index = next((i for i, item in enumerate(sys.path) if _normalize(item) == before_norm), None)
    if before_index is None:
        sys.path.append(value)
    else:
        sys.path.insert(before_index, value)


def _prefer_base_site_packages() -> None:
    user_site = site.getusersitepackages()
    base_site_candidates = [
        str(Path(sys.base_prefix) / "Lib" / "site-packages"),
        str(Path(sys.prefix) / "Lib" / "site-packages"),
    ]
    for candidate in base_site_candidates:
        _move_before(candidate, user_site)


def _configure_project_caches() -> None:
    project_root = Path(__file__).resolve().parent
    cache_root = project_root / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_paths = {
        "MPLCONFIGDIR": cache_root / "matplotlib",
        "PADDLE_PDX_CACHE_HOME": cache_root / "paddlex",
        "PADDLE_HOME": cache_root / "paddle",
        "PADDLEOCR_HOME": cache_root / "paddleocr",
        "KONGGU_OCR_TEXT_CACHE": cache_root / "pdf_text",
    }
    for env_name, path in cache_paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(env_name, str(path))
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


_prefer_base_site_packages()
_configure_project_caches()

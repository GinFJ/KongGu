"""Offline resource status and repair for the Konggu desktop build."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import shutil
import sys
from typing import Any

from app.privacy import safe_error_message


APP_NAME = "Konggu"
MANIFEST_NAME = "offline_manifest.json"
REQUIRED_OCR_MODELS = ("PP-OCRv4_mobile_det", "PP-OCRv4_mobile_rec")
_DLL_DIRECTORY_HANDLES: list[Any] = []


@dataclass(slots=True)
class ResourcePaths:
    bundled_root: Path
    app_data_root: Path
    user_resources_root: Path
    ocr_models_root: Path
    cache_root: Path
    logs_root: Path


def resolve_resource_paths() -> ResourcePaths:
    bundled_root = _bundled_root()
    app_data_root = _app_data_root()
    return ResourcePaths(
        bundled_root=bundled_root,
        app_data_root=app_data_root,
        user_resources_root=app_data_root / "resources",
        ocr_models_root=app_data_root / "ocr_models",
        cache_root=app_data_root / "cache",
        logs_root=app_data_root / "logs",
    )


def configure_offline_environment() -> ResourcePaths:
    """Point Paddle/Konggu caches and OCR lookup to local writable directories."""

    paths = resolve_resource_paths()
    for path in (paths.user_resources_root, paths.ocr_models_root, paths.cache_root, paths.logs_root):
        path.mkdir(parents=True, exist_ok=True)
    _purge_expired_processing_cache(paths)
    os.environ.setdefault("KONGGU_RESOURCE_ROOT", str(paths.user_resources_root))
    os.environ.setdefault("KONGGU_OCR_MODEL_DIR", str(paths.ocr_models_root))
    os.environ.setdefault("KONGGU_OCR_TEXT_CACHE", str(paths.cache_root / "pdf_text"))
    os.environ.setdefault("KONGGU_OCR_LAYOUT_CACHE", str(paths.cache_root / "pdf_layout"))
    os.environ.setdefault("KONGGU_PARSE_CACHE", str(paths.cache_root / "parsed_blocks"))
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(paths.ocr_models_root.parent))
    os.environ.setdefault("PADDLE_HOME", str(paths.cache_root / "paddle"))
    os.environ.setdefault("PADDLEOCR_HOME", str(paths.cache_root / "paddleocr"))
    os.environ.setdefault("MPLCONFIGDIR", str(paths.cache_root / "matplotlib"))
    _configure_paddle_dll_paths()
    os.environ.pop("KONGGU_ALLOW_OCR_MODEL_DOWNLOAD", None)
    return paths


def resource_status(*, check_runtime: bool = False) -> dict[str, Any]:
    """Return offline resource integrity details after preparing local cache paths."""

    paths = configure_offline_environment()
    manifest = load_manifest(paths.bundled_root)
    missing: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    entries = manifest.get("entries", [])
    for entry in entries:
        try:
            target = _target_path(paths, entry)
        except ValueError:
            invalid.append(_entry_status(entry, None, "invalid-manifest-path"))
            continue
        issue = _validate_entry(target, entry)
        if issue == "missing":
            missing.append(_entry_status(entry, target, issue))
        elif issue:
            invalid.append(_entry_status(entry, target, issue))

    ocr = _ocr_status(paths.ocr_models_root)
    if check_runtime and ocr["ready"]:
        ocr["runtime"] = _ocr_runtime_status()
        ocr["ready"] = bool(ocr["ready"] and ocr["runtime"].get("ready"))
    elif check_runtime:
        ocr["runtime"] = {"ready": False, "stage": "model-check", "error": "OCR 模型文件缺失或未通过校验。"}
    ready = not missing and not invalid and ocr["ready"]
    return {
        "ok": True,
        "ready": ready,
        "storage": "本机应用数据目录（路径已隐藏）",
        "manifest_version": manifest.get("version", 1),
        "entry_count": len(entries),
        "missing": missing,
        "invalid": invalid,
        "ocr": ocr,
    }


def repair_resources() -> dict[str, Any]:
    """Repair local resources from bundled files only; never access the network."""

    paths = resolve_resource_paths()
    for path in (paths.user_resources_root, paths.ocr_models_root, paths.cache_root, paths.logs_root):
        path.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(paths.bundled_root)
    copied: list[str] = []
    failed: list[dict[str, str]] = []
    for entry in manifest.get("entries", []):
        try:
            target = _target_path(paths, entry)
            source = _safe_child(paths.bundled_root, entry.get("source"), "source")
        except ValueError as exc:
            failed.append(
                {
                    "source": safe_error_message(entry.get("source"), "未知资源"),
                    "target": safe_error_message(entry.get("target"), "未知目标"),
                    "error": type(exc).__name__,
                }
            )
            continue
        issue = _validate_entry(target, entry)
        if not issue:
            continue
        try:
            _copy_entry(source, target)
            copied.append(str(entry.get("target") or ""))
        except Exception as exc:
            failed.append(
                {
                    "source": str(entry.get("source") or ""),
                    "target": str(entry.get("target") or ""),
                    "error": type(exc).__name__,
                }
            )

    status = resource_status(check_runtime=True)
    return {"ok": not failed, "copied": copied, "failed": failed, "status": status}


def clear_processing_cache(paths: ResourcePaths | None = None) -> list[str]:
    """Remove only Konggu-generated processing caches under the app data root."""

    resolved = paths or resolve_resource_paths()
    cleared: list[str] = []
    for name in ("pdf_text", "pdf_layout", "parsed_blocks"):
        target = _safe_child(resolved.cache_root, name, "cache")
        if target.exists():
            shutil.rmtree(target)
            cleared.append(name)
    return cleared


def _purge_expired_processing_cache(paths: ResourcePaths, retention_days: int = 30) -> None:
    cutoff = datetime.now(UTC).timestamp() - timedelta(days=max(1, retention_days)).total_seconds()
    for name in ("pdf_text", "pdf_layout", "parsed_blocks"):
        root = _safe_child(paths.cache_root, name, "cache")
        if not root.exists():
            continue
        for item in sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
                elif item.is_dir() and not any(item.iterdir()):
                    item.rmdir()
            except OSError:
                continue


def load_manifest(bundled_root: Path | None = None) -> dict[str, Any]:
    root = bundled_root or _bundled_root()
    candidates = [
        root / "resources" / MANIFEST_NAME,
        root / MANIFEST_NAME,
        Path(__file__).resolve().parents[1] / "resources" / MANIFEST_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                break
    return {"version": 1, "generated_at": "", "entries": []}


def build_manifest(root: Path, sources: list[str]) -> dict[str, Any]:
    """Build a manifest for release-time offline resources."""

    entries: list[dict[str, Any]] = []
    for source in sources:
        source_path = root / source
        if not source_path.exists():
            continue
        for file in _iter_files(source_path):
            rel = file.relative_to(root).as_posix()
            target = _default_target(rel)
            entries.append(
                {
                    "source": rel,
                    "target": target,
                    "sha256": _sha256(file),
                    "size": file.stat().st_size,
                    "kind": _entry_kind(target),
                }
            )
    return {
        "version": 1,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "entries": sorted(entries, key=lambda item: item["target"]),
    }


def _bundled_root() -> Path:
    configured = os.environ.get("KONGGU_BUNDLED_RESOURCE_ROOT")
    if configured:
        return Path(configured)
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _app_data_root() -> Path:
    configured = os.environ.get("KONGGU_APP_DATA_ROOT")
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def _configure_paddle_dll_paths() -> None:
    candidates: list[Path] = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "paddle" / "libs")
    candidates.append(Path(__file__).resolve().parents[1] / "paddle" / "libs")
    for path in candidates:
        if not path.exists():
            continue
        current_path = os.environ.get("PATH", "")
        path_text = str(path)
        if path_text.lower() not in {item.lower() for item in current_path.split(os.pathsep) if item}:
            os.environ["PATH"] = path_text + os.pathsep + current_path
        if hasattr(os, "add_dll_directory"):
            try:
                handle = os.add_dll_directory(path_text)
            except OSError:
                continue
            _DLL_DIRECTORY_HANDLES.append(handle)


def _target_path(paths: ResourcePaths, entry: dict[str, Any]) -> Path:
    target = _manifest_relative_path(entry.get("target"), "target")
    normalized = target.as_posix()
    if normalized.startswith("ocr_models/"):
        return _safe_child(paths.ocr_models_root, target.relative_to("ocr_models"), "target")
    if normalized.startswith("resources/"):
        return _safe_child(paths.user_resources_root, target.relative_to("resources"), "target")
    return _safe_child(paths.user_resources_root, target, "target")


def _manifest_relative_path(value: Any, label: str) -> Path:
    """Parse a manifest path without allowing absolute or parent traversal."""

    text = str(value or "").replace("\\", "/")
    windows = PureWindowsPath(text)
    posix = PurePosixPath(text)
    if not text or posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f"资源清单 {label} 路径必须是相对路径。")
    parts = tuple(part for part in posix.parts if part not in ("", "."))
    if not parts or ".." in parts:
        raise ValueError(f"资源清单 {label} 路径包含非法父级跳转。")
    return Path(*parts)


def _safe_child(root: Path, relative: Any, label: str) -> Path:
    relative_path = _manifest_relative_path(relative, label)
    root_resolved = root.resolve()
    target = (root_resolved / relative_path).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"资源清单 {label} 路径超出允许目录。") from exc
    return target


def _validate_entry(target: Path, entry: dict[str, Any]) -> str:
    if not target.exists():
        return "missing"
    if target.is_dir():
        return ""
    expected_size = entry.get("size")
    if expected_size is not None and target.stat().st_size != int(expected_size):
        return "size-mismatch"
    expected_hash = str(entry.get("sha256") or "")
    if expected_hash and _sha256(target) != expected_hash:
        return "hash-mismatch"
    return ""


def _entry_status(entry: dict[str, Any], target: Path | None, issue: str) -> dict[str, Any]:
    return {
        "target": safe_error_message(entry.get("target") or (target.name if target else "")),
        "source": safe_error_message(entry.get("source") or ""),
        "kind": entry.get("kind", ""),
        "issue": issue,
    }


def _ocr_status(root: Path) -> dict[str, Any]:
    models = []
    for name in REQUIRED_OCR_MODELS:
        path = root / name
        valid = _is_valid_paddle_model_dir(path)
        models.append({"name": name, "path": name, "exists": path.exists(), "valid": valid})
    return {"ready": all(item["valid"] for item in models), "models": models}


def _ocr_runtime_status() -> dict[str, Any]:
    try:
        from core import schedule_core

        return schedule_core.check_ocr_runtime(run_probe=True)
    except Exception as exc:
        return {"ready": False, "stage": "status-check", "error": safe_error_message(exc)}


def _copy_entry(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _iter_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return [item for item in path.rglob("*") if item.is_file()]


def _default_target(rel: str) -> str:
    normalized = rel.replace("\\", "/")
    if normalized.startswith("resources/ocr_models/"):
        return normalized.removeprefix("resources/")
    if normalized.startswith("resources/"):
        return normalized
    if normalized.startswith("config/") or normalized.startswith("assets/"):
        return f"resources/{normalized}"
    return normalized


def _entry_kind(target: str) -> str:
    if target.startswith("ocr_models/"):
        return "ocr_model"
    if target.startswith("resources/config/"):
        return "config"
    if target.startswith("resources/assets/"):
        return "asset"
    return "resource"


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_valid_paddle_model_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    names = {item.name for item in path.iterdir()}
    return bool(
        names.intersection({"inference.yml", "inference.yaml", "model.yml", "config.yml"})
        or any(name.endswith(".pdmodel") or name.endswith(".json") for name in names)
    )

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
# Directories whose files must be listed in the offline manifest; shared by the
# release preparation script and the static release gate.
OFFLINE_SOURCE_DIRS = ("config", "assets", "resources/ocr_models")
_DLL_DIRECTORY_HANDLES: list[Any] = []


class OfflineManifestError(RuntimeError):
    """The bundled offline resource manifest is missing or cannot be trusted."""

    def __init__(self, message: str, *, issue: str = "invalid-manifest") -> None:
        super().__init__(message)
        self.issue = issue


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
        ocr_models_root=_ocr_models_root(app_data_root),
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
    os.environ["KONGGU_OCR_MODEL_DIR"] = str(paths.ocr_models_root)
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
    manifest, manifest_issue, manifest_error = _load_manifest_for_status(paths.bundled_root)
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
        ocr["runtime"] = {
            "ready": False,
            "stage": "model-check",
            "error": ocr.get("error") or "OCR 模型文件缺失或未通过校验。",
        }
    ready = not missing and not invalid and not manifest_issue and ocr["ready"]
    return {
        "ok": True,
        "ready": ready,
        "storage": "本机应用数据目录（路径已隐藏）",
        "manifest_version": manifest.get("version", 1),
        "manifest_issue": manifest_issue,
        "manifest_error": manifest_error,
        "entry_count": len(entries),
        "missing": missing,
        "invalid": invalid,
        "ocr": ocr,
    }


def _load_manifest_for_status(bundled_root: Path) -> tuple[dict[str, Any], str, str]:
    """Load the manifest without failing open: a broken manifest is a real issue."""

    try:
        return load_manifest(bundled_root), "", ""
    except OfflineManifestError as exc:
        return (
            {"version": 1, "generated_at": "", "entries": []},
            exc.issue,
            safe_error_message(exc),
        )


def repair_resources() -> dict[str, Any]:
    """Repair local resources from bundled files only; never access the network."""

    paths = resolve_resource_paths()
    for path in (paths.user_resources_root, paths.ocr_models_root, paths.cache_root, paths.logs_root):
        path.mkdir(parents=True, exist_ok=True)

    try:
        manifest = load_manifest(paths.bundled_root)
    except OfflineManifestError as exc:
        return {
            "ok": False,
            "copied": [],
            "failed": [{"source": MANIFEST_NAME, "target": "", "error": exc.issue}],
            "error": safe_error_message(exc),
            "status": resource_status(check_runtime=True),
        }
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
        if issue == "invalid-manifest-entry":
            # Never copy over a target the manifest cannot describe; report it instead.
            failed.append(
                {
                    "source": safe_error_message(entry.get("source"), "未知资源"),
                    "target": safe_error_message(entry.get("target"), "未知目标"),
                    "error": issue,
                }
            )
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
    """Load the bundled offline manifest, failing closed when it is unusable.

    A missing or corrupt manifest must not be treated as an empty manifest:
    that would report ready resources that were never verified, and the repair
    path would silently do nothing.
    """

    root = bundled_root or _bundled_root()
    candidates = [
        root / "resources" / MANIFEST_NAME,
        root / MANIFEST_NAME,
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            raise OfflineManifestError("离线资源清单无法解析，已停止资源校验。") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise OfflineManifestError("离线资源清单格式不正确，已停止资源校验。")
        return payload
    raise OfflineManifestError(
        "未找到离线资源清单，无法校验随包资源。",
        issue="missing-manifest",
    )


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


def _ocr_models_root(app_data_root: Path) -> Path:
    """Keep OCR model paths ASCII-compatible for Paddle on Windows."""

    configured_value = str(os.environ.get("KONGGU_OCR_MODEL_DIR") or "").strip()
    configured = Path(configured_value) if configured_value else None
    default_root = app_data_root / "ocr_models"

    if configured is not None and (not _requires_ascii_path() or _is_ascii_path(configured)):
        return configured
    if not _requires_ascii_path() or _is_ascii_path(default_root):
        return default_root

    candidates: list[Path] = []
    for env_name in ("PROGRAMDATA", "PUBLIC"):
        base = str(os.environ.get(env_name) or "").strip()
        if not base:
            continue
        candidate = Path(base) / APP_NAME / "ocr_models"
        if _is_ascii_path(candidate):
            candidates.append(candidate)
    for candidate in candidates:
        # A machine policy or a denied ACL can make ProgramData unwritable for
        # standard users; fall back to the next ASCII root instead of failing
        # the whole sidecar at startup.
        if _usable_directory(candidate):
            return candidate
    return configured or default_root


def _usable_directory(path: Path) -> bool:
    if path.is_dir():
        return True
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def _requires_ascii_path() -> bool:
    return sys.platform == "win32"


def _is_ascii_path(path: Path) -> bool:
    return str(path).isascii()


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
        # Manifest entries describe files; a directory at the target is not a
        # verified copy and must be repaired instead of counted as ready.
        return "not-a-file"
    try:
        expected_size = int(entry["size"])
    except (KeyError, TypeError, ValueError):
        return "invalid-manifest-entry"
    expected_hash = str(entry.get("sha256") or "").strip().lower()
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        return "invalid-manifest-entry"
    if target.stat().st_size != expected_size:
        return "size-mismatch"
    if _sha256(target) != expected_hash:
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

    path_compatible = not _requires_ascii_path() or _is_ascii_path(root)
    if not path_compatible:
        issue = "path-incompatible"
        error = "OCR 模型目录包含非 ASCII 字符，当前识别引擎无法读取。"
    elif any(not item["exists"] for item in models):
        issue = "missing"
        error = "OCR 模型文件缺失。"
    elif any(not item["valid"] for item in models):
        issue = "invalid"
        error = "OCR 模型文件未通过校验。"
    else:
        issue = ""
        error = ""
    return {
        "ready": path_compatible and all(item["valid"] for item in models),
        "issue": issue,
        "error": error,
        "models": models,
    }


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
        return
    if target.is_dir():
        # Replace a leftover directory that occupies a file target.
        shutil.rmtree(target)
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

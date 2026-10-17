"""Prepare Konggu offline resources before building the desktop installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.offline_resources import OFFLINE_SOURCE_DIRS, build_manifest  # noqa: E402

OCR_CONFIG = ROOT / "config" / "ocr_models.json"
OFFLINE_ROOT = ROOT / "resources"
OCR_TARGET = OFFLINE_ROOT / "ocr_models"
DOWNLOAD_CACHE = ROOT / "downloads" / "ocr"
MANIFEST_PATH = OFFLINE_ROOT / "offline_manifest.json"
MODEL_CONFIG_FILES = ("inference.yml", "inference.yaml", "model.yml", "config.yml")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare offline resources for the Konggu desktop installer.")
    parser.add_argument("--download-ocr", action="store_true", help="Download configured OCR models into resources/ocr_models.")
    parser.add_argument("--allow-missing-ocr", action="store_true", help="Generate a manifest even when OCR models are absent.")
    args = parser.parse_args()

    if args.download_ocr:
        _download_ocr_models()
    _assert_ocr_models_usable(allow_missing=args.allow_missing_ocr)

    OFFLINE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(ROOT, list(OFFLINE_SOURCE_DIRS))
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Offline manifest written: {MANIFEST_PATH}")
    print(f"Manifest entries: {len(manifest['entries'])}")


def _download_ocr_models() -> None:
    config = json.loads(OCR_CONFIG.read_text(encoding="utf-8"))
    OCR_TARGET.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_CACHE.mkdir(parents=True, exist_ok=True)
    for model in config["models"]:
        name = model["name"]
        url = model["url"]
        archive = DOWNLOAD_CACHE / f"{name}.tar"
        expected_sha256 = model.get("archive_sha256")
        expected_size = model.get("archive_size")
        if not _archive_is_valid(archive, expected_sha256, expected_size):
            print(f"Downloading {name} ...")
            _download(url, archive)
        _verify_archive(archive, expected_sha256, expected_size)
        _extract_model(archive, name, OCR_TARGET)


def _download(url: str, dest: Path) -> None:
    temp_dest = dest.with_suffix(dest.suffix + ".tmp")
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                with temp_dest.open("wb") as file:
                    shutil.copyfileobj(response, file)
            temp_dest.replace(dest)
            return
        except Exception as exc:  # pragma: no cover - network-only branch
            last_error = exc
            temp_dest.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(3 * attempt)
    raise RuntimeError(f"Failed to download {url}") from last_error


def _extract_model(archive_path: Path, model_name: str, target_root: Path) -> Path:
    model_dir = target_root / model_name
    if model_dir.exists():
        shutil.rmtree(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path) as tar:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _safe_extract(tar, tmp_path)
            children = [child for child in tmp_path.iterdir()]
            source = children[0] if len(children) == 1 and children[0].is_dir() else tmp_path
            for item in source.iterdir():
                destination = model_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, destination)
                else:
                    shutil.copy2(item, destination)
    return model_dir


def _archive_is_valid(path: Path, expected_sha256: str | None, expected_size: int | None) -> bool:
    if not path.exists():
        return False
    try:
        _verify_archive(path, expected_sha256, expected_size)
        return True
    except RuntimeError:
        return False


def _verify_archive(path: Path, expected_sha256: str | None, expected_size: int | None) -> None:
    if expected_size is not None and path.stat().st_size != int(expected_size):
        raise RuntimeError(f"OCR archive size mismatch: {path}")
    if expected_sha256 and _sha256(path) != expected_sha256:
        raise RuntimeError(f"OCR archive hash mismatch: {path}")


def _safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    base = destination.resolve()
    for member in tar.getmembers():
        target = (destination / member.name).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise RuntimeError(f"Unsafe tar member path: {member.name}") from exc
    tar.extractall(destination, filter="data")


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _assert_ocr_models_present() -> None:
    """Backward-compatible alias for the stricter payload gate."""

    _assert_ocr_models_usable(allow_missing=False)


def _assert_ocr_models_usable(*, allow_missing: bool) -> None:
    """Fail the release build when OCR model payloads are absent or corrupt.

    A manifest generated from an empty or truncated ``inference.json`` is
    self-consistent, so every later integrity gate passes while OCR can never
    work and ``resources.repair`` cannot fix it (the hash matches the manifest).
    """

    config = json.loads(OCR_CONFIG.read_text(encoding="utf-8"))
    problems: list[str] = []
    for model in config["models"]:
        name = str(model["name"])
        model_dir = OCR_TARGET / name
        if not model_dir.exists():
            if not allow_missing:
                problems.append(f"{name}: 模型目录缺失")
            continue
        problems.extend(f"{name}: {issue}" for issue in _model_payload_issues(model_dir))
    if problems:
        raise SystemExit(
            "Invalid offline OCR model payload: "
            + "; ".join(problems)
            + ". Re-run with --download-ocr before release; --allow-missing-ocr is only for development."
        )


def _model_payload_issues(model_dir: Path) -> list[str]:
    """Return concrete problems found in an extracted Paddle inference model directory."""

    if not model_dir.is_dir():
        return ["模型目录不存在"]
    files = {item.name: item for item in model_dir.iterdir() if item.is_file()}
    issues: list[str] = []
    config_names = [name for name in MODEL_CONFIG_FILES if name in files]
    if not config_names or files[config_names[0]].stat().st_size == 0:
        issues.append("缺少非空模型配置（inference.yml 等）")
    weights = [item for name, item in files.items() if name.endswith(".pdiparams")]
    if not weights:
        issues.append("缺少 .pdiparams 权重文件")
    elif any(item.stat().st_size == 0 for item in weights):
        issues.append(".pdiparams 权重文件为空")
    graph = [item for name, item in files.items() if name.endswith(".pdmodel") and item.stat().st_size > 0]
    inference_json = files.get("inference.json")
    if inference_json is not None:
        try:
            payload = json.loads(inference_json.read_text(encoding="utf-8"))
        except Exception:
            issues.append("inference.json 不是有效 JSON")
        else:
            if not isinstance(payload, dict) or not payload:
                issues.append("inference.json 内容为空")
    elif not graph:
        issues.append("缺少模型结构文件（inference.json 或 .pdmodel）")
    return issues


if __name__ == "__main__":
    main()

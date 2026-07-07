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

from app.offline_resources import build_manifest  # noqa: E402

OCR_CONFIG = ROOT / "config" / "ocr_models.json"
OFFLINE_ROOT = ROOT / "resources"
OCR_TARGET = OFFLINE_ROOT / "ocr_models"
DOWNLOAD_CACHE = ROOT / "downloads" / "ocr"
MANIFEST_PATH = OFFLINE_ROOT / "offline_manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare offline resources for the Konggu desktop installer.")
    parser.add_argument("--download-ocr", action="store_true", help="Download configured OCR models into resources/ocr_models.")
    parser.add_argument("--allow-missing-ocr", action="store_true", help="Generate a manifest even when OCR models are absent.")
    args = parser.parse_args()

    if args.download_ocr:
        _download_ocr_models()
    elif not args.allow_missing_ocr:
        _assert_ocr_models_present()

    OFFLINE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(ROOT, ["config", "assets", "resources/ocr_models"])
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
    missing = []
    config = json.loads(OCR_CONFIG.read_text(encoding="utf-8"))
    for model in config["models"]:
        model_dir = OCR_TARGET / model["name"]
        if not model_dir.exists() or not any(model_dir.iterdir()):
            missing.append(model["name"])
    if missing:
        raise SystemExit(
            "Missing offline OCR models: "
            + ", ".join(missing)
            + ". Run with --download-ocr before release, or use --allow-missing-ocr for development."
        )


if __name__ == "__main__":
    main()

"""回归测试：离线资源校验、发布门禁与构建环境一致性（平台/交付审计）。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import offline_resources as resources  # noqa: E402
from app.offline_resources import (  # noqa: E402
    OFFLINE_SOURCE_DIRS,
    REQUIRED_OCR_MODELS,
    OfflineManifestError,
    _validate_entry,
    build_manifest,
    load_manifest,
    repair_resources,
    resource_status,
)

import tools.prepare_offline_resources as prepare  # noqa: E402
import tools.verify_release_bundle as release_gate  # noqa: E402


def _write_manifest(root: Path, sources: list[str]) -> dict:
    manifest = build_manifest(root, sources)
    target = root / "resources" / "offline_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def _write_ocr_models(root: Path) -> None:
    for name in REQUIRED_OCR_MODELS:
        model_dir = root / "resources" / "ocr_models" / name
        model_dir.mkdir(parents=True)
        (model_dir / "inference.yml").write_text("model: fake", encoding="utf-8")
        (model_dir / "inference.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (model_dir / "inference.pdiparams").write_text("weights", encoding="utf-8")


def _offline_fixture(tmp_path: Path, monkeypatch, *, with_models: bool = True) -> tuple[Path, Path]:
    bundled = tmp_path / "bundled"
    app_data = tmp_path / "appdata"
    (bundled / "config").mkdir(parents=True)
    (bundled / "config" / "period_time.json").write_text('{"periods":[]}', encoding="utf-8")
    sources = ["config"]
    if with_models:
        _write_ocr_models(bundled)
        sources.append("resources/ocr_models")
    _write_manifest(bundled, sources)
    monkeypatch.setenv("KONGGU_BUNDLED_RESOURCE_ROOT", str(bundled))
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))
    monkeypatch.delenv("KONGGU_OCR_MODEL_DIR", raising=False)
    monkeypatch.setattr(resources, "_requires_ascii_path", lambda: False)
    monkeypatch.setattr(
        resources,
        "_ocr_runtime_status",
        lambda: {"ready": True, "stage": "ready", "error": ""},
    )
    return bundled, app_data


def test_missing_manifest_is_reported_instead_of_ready(tmp_path: Path, monkeypatch):
    bundled, _ = _offline_fixture(tmp_path, monkeypatch)
    (bundled / "resources" / "offline_manifest.json").unlink()

    with pytest.raises(OfflineManifestError) as excinfo:
        load_manifest(bundled)
    assert excinfo.value.issue == "missing-manifest"

    status = resource_status(check_runtime=True)
    assert status["ready"] is False
    assert status["manifest_issue"] == "missing-manifest"
    assert status["entry_count"] == 0
    repaired = repair_resources()
    assert repaired["ok"] is False
    assert repaired["failed"][0]["error"] == "missing-manifest"


def test_corrupt_manifest_is_reported_instead_of_ready(tmp_path: Path, monkeypatch):
    bundled, _ = _offline_fixture(tmp_path, monkeypatch)
    (bundled / "resources" / "offline_manifest.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(OfflineManifestError) as excinfo:
        load_manifest(bundled)
    assert excinfo.value.issue == "invalid-manifest"

    status = resource_status(check_runtime=True)
    assert status["ready"] is False
    assert status["manifest_issue"] == "invalid-manifest"
    repaired = repair_resources()
    assert repaired["ok"] is False
    assert repaired["copied"] == []
    assert repaired["failed"][0]["error"] == "invalid-manifest"


def test_manifest_entry_without_size_or_hash_is_not_trusted(tmp_path: Path, monkeypatch):
    bundled, app_data = _offline_fixture(tmp_path, monkeypatch, with_models=False)
    target = app_data / "resources" / "config" / "period_time.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"periods":[]}', encoding="utf-8")

    manifest = json.loads((bundled / "resources" / "offline_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0].pop("sha256")
    (bundled / "resources" / "offline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    status = resource_status()
    assert status["ready"] is False
    assert status["invalid"][0]["issue"] == "invalid-manifest-entry"
    repaired = repair_resources()
    # 修复操作本身完成（ok=True），但资源未就绪且失败条目可诊断。
    assert repaired["ok"] is True
    assert repaired["ready"] is False
    assert repaired["copied"] == []
    assert repaired["failed"][0]["error"] == "invalid-manifest-entry"


def test_malformed_manifest_size_is_an_invalid_entry_not_a_crash(tmp_path: Path, monkeypatch):
    bundled, app_data = _offline_fixture(tmp_path, monkeypatch, with_models=False)
    target = app_data / "resources" / "config" / "period_time.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"periods":[]}', encoding="utf-8")

    manifest = json.loads((bundled / "resources" / "offline_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["size"] = "not-a-number"
    (bundled / "resources" / "offline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    status = resource_status()
    assert status["invalid"][0]["issue"] == "invalid-manifest-entry"


def test_directory_where_file_expected_is_flagged_and_repaired(tmp_path: Path, monkeypatch):
    bundled, app_data = _offline_fixture(tmp_path, monkeypatch, with_models=False)
    entry = json.loads((bundled / "resources" / "offline_manifest.json").read_text(encoding="utf-8"))["entries"][0]
    target = app_data / "resources" / "config" / "period_time.json"
    target.mkdir(parents=True)

    assert _validate_entry(target, entry) == "not-a-file"
    status = resource_status()
    assert status["ready"] is False
    assert status["invalid"][0]["issue"] == "not-a-file"

    repaired = repair_resources()
    assert repaired["ok"] is True
    assert target.is_file()
    assert _validate_entry(target, entry) == ""

    repaired_again = repair_resources()
    assert repaired_again["ok"] is True
    assert repaired_again["copied"] == []


def test_manifest_target_escape_is_rejected_without_writing_outside(tmp_path: Path, monkeypatch):
    bundled, app_data = _offline_fixture(tmp_path, monkeypatch, with_models=False)
    outside = tmp_path / "outside.txt"
    manifest = {
        "version": 1,
        "generated_at": "",
        "entries": [
            {
                "source": "config/period_time.json",
                "target": "../outside.txt",
                "sha256": hashlib.sha256((bundled / "config" / "period_time.json").read_bytes()).hexdigest(),
                "size": (bundled / "config" / "period_time.json").stat().st_size,
                "kind": "config",
            }
        ],
    }
    (bundled / "resources" / "offline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    status = resource_status()
    assert status["ready"] is False
    assert status["invalid"][0]["issue"] == "invalid-manifest-path"
    repaired = repair_resources()
    assert repaired["ok"] is True
    assert repaired["ready"] is False
    assert repaired["failed"][0]["error"] == "ValueError"
    assert not outside.exists()
    assert not (app_data / "outside.txt").exists()


def test_manifest_entries_that_are_not_objects_fail_closed(tmp_path: Path, monkeypatch):
    bundled, app_data = _offline_fixture(tmp_path, monkeypatch, with_models=False)
    (bundled / "resources" / "offline_manifest.json").write_text(
        json.dumps({"version": 1, "generated_at": "", "entries": ["x"]}), encoding="utf-8"
    )

    status = resource_status()

    assert status["ready"] is False
    assert status["manifest_issue"] == "invalid-manifest-entry"


def test_ocr_models_root_falls_back_to_public_when_programdata_is_unusable(tmp_path: Path, monkeypatch):
    app_data = tmp_path / "用户目录" / "Konggu"
    blocked = tmp_path / "programdata-blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    public_root = tmp_path / "Public"
    monkeypatch.setattr(resources, "_requires_ascii_path", lambda: True)
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))
    monkeypatch.setenv("PROGRAMDATA", str(blocked))
    monkeypatch.setenv("PUBLIC", str(public_root))
    monkeypatch.delenv("KONGGU_OCR_MODEL_DIR", raising=False)

    paths = resources.resolve_resource_paths()

    assert paths.ocr_models_root == public_root / "Konggu" / "ocr_models"


def test_ocr_status_reports_incompatibility_when_no_ascii_root_is_usable(tmp_path: Path, monkeypatch):
    app_data = tmp_path / "用户目录" / "Konggu"
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(resources, "_requires_ascii_path", lambda: True)
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))
    monkeypatch.setenv("PROGRAMDATA", str(blocked))
    monkeypatch.setenv("PUBLIC", str(blocked))
    monkeypatch.delenv("KONGGU_OCR_MODEL_DIR", raising=False)

    paths = resources.resolve_resource_paths()

    assert paths.ocr_models_root == app_data / "ocr_models"
    assert resources._ocr_status(paths.ocr_models_root)["issue"] == "path-incompatible"


# --- 构建期 OCR 载荷校验 ---------------------------------------------------


def _prepare_model_fixture(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "ocr_models.json"
    config.write_text(
        json.dumps({"models": [{"name": "det_model"}, {"name": "rec_model"}]}),
        encoding="utf-8",
    )
    target = tmp_path / "ocr_target"
    monkeypatch.setattr(prepare, "OCR_CONFIG", config)
    monkeypatch.setattr(prepare, "OCR_TARGET", target)
    return target


def _write_model_dir(root: Path, name: str, *, yml: str, inference_json: str, weights: str) -> None:
    model_dir = root / name
    model_dir.mkdir(parents=True)
    (model_dir / "inference.yml").write_text(yml, encoding="utf-8")
    if inference_json is not None:
        (model_dir / "inference.json").write_text(inference_json, encoding="utf-8")
    if weights is not None:
        (model_dir / "inference.pdiparams").write_text(weights, encoding="utf-8")


def test_prepare_accepts_valid_model_payload(tmp_path: Path, monkeypatch):
    target = _prepare_model_fixture(tmp_path, monkeypatch)
    for name in ("det_model", "rec_model"):
        _write_model_dir(target, name, yml="model: ok", inference_json='{"k": 1}', weights="weights")

    prepare._assert_ocr_models_usable(allow_missing=False)


def test_prepare_rejects_empty_inference_json(tmp_path: Path, monkeypatch):
    target = _prepare_model_fixture(tmp_path, monkeypatch)
    for name in ("det_model", "rec_model"):
        _write_model_dir(target, name, yml="", inference_json="", weights="weights")

    issues = prepare._model_payload_issues(target / "det_model")
    assert any("inference.json" in issue for issue in issues)
    with pytest.raises(SystemExit):
        prepare._assert_ocr_models_usable(allow_missing=False)


def test_prepare_rejects_missing_weight_file(tmp_path: Path, monkeypatch):
    target = _prepare_model_fixture(tmp_path, monkeypatch)
    for name in ("det_model", "rec_model"):
        _write_model_dir(target, name, yml="model: ok", inference_json='{"k": 1}', weights=None)

    with pytest.raises(SystemExit):
        prepare._assert_ocr_models_usable(allow_missing=False)


def test_prepare_missing_models_only_allowed_in_development(tmp_path: Path, monkeypatch):
    _prepare_model_fixture(tmp_path, monkeypatch)

    prepare._assert_ocr_models_usable(allow_missing=True)
    with pytest.raises(SystemExit):
        prepare._assert_ocr_models_usable(allow_missing=False)


# --- 发布静态门禁：版本、清单覆盖与路径 ------------------------------------


def _write_release_versions(root: Path, version: str) -> None:
    (root / "src-tauri").mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (root / "package-lock.json").write_text(
        json.dumps({"version": version, "packages": {"": {"version": version}}}),
        encoding="utf-8",
    )
    (root / "src-tauri" / "tauri.conf.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (root / "src-tauri" / "Cargo.toml").write_text(
        f'[package]\nname = "konggu"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "src-tauri" / "Cargo.lock").write_text(
        f'[[package]]\nname = "konggu"\nversion = "{version}"\n', encoding="utf-8"
    )


def test_release_gate_accepts_consistent_versions(tmp_path: Path):
    _write_release_versions(tmp_path, "0.5.8")

    checks = dict((name, passed) for name, passed, _ in release_gate._version_checks(tmp_path))

    assert checks["version:package.json"]
    assert checks["version:package-lock.json:packages"]
    assert checks["version:src-tauri/tauri.conf.json"]
    assert checks["version:src-tauri/Cargo.toml"]
    assert checks["version:src-tauri/Cargo.lock"]


def test_release_gate_detects_version_drift(tmp_path: Path):
    _write_release_versions(tmp_path, "0.5.8")
    (tmp_path / "src-tauri" / "tauri.conf.json").write_text(json.dumps({"version": "0.5.7"}), encoding="utf-8")

    checks = dict((name, passed) for name, passed, _ in release_gate._version_checks(tmp_path))

    assert checks["version:src-tauri/tauri.conf.json"] is False
    assert checks["version:package.json"] is True


def test_release_gate_rejects_manifest_traversal_paths():
    manifest = {
        "entries": [
            {"source": "config/a.json", "target": "resources/config/a.json", "size": 1, "sha256": "a" * 64},
            {"source": "config/b.json", "target": "../outside.json", "size": 1, "sha256": "b" * 64},
        ]
    }

    checks = dict((name, passed) for name, passed, _ in release_gate._manifest_path_checks(manifest))

    assert checks["offline-manifest:paths"] is False


def test_release_gate_detects_unlisted_bundled_file(tmp_path: Path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "b.json").write_text("{}", encoding="utf-8")
    manifest = {
        "entries": [
            {
                "source": "config/a.json",
                "target": "resources/config/a.json",
                "size": (tmp_path / "config" / "a.json").stat().st_size,
                "sha256": hashlib.sha256((tmp_path / "config" / "a.json").read_bytes()).hexdigest(),
                "kind": "config",
            }
        ]
    }

    checks = dict((name, passed) for name, passed, _ in release_gate._manifest_coverage_checks(tmp_path, manifest))
    assert checks["offline-manifest:coverage"] is False

    manifest["entries"].append(
        {
            "source": "config/b.json",
            "target": "resources/config/b.json",
            "size": (tmp_path / "config" / "b.json").stat().st_size,
            "sha256": hashlib.sha256((tmp_path / "config" / "b.json").read_bytes()).hexdigest(),
            "kind": "config",
        }
    )
    checks = dict((name, passed) for name, passed, _ in release_gate._manifest_coverage_checks(tmp_path, manifest))
    assert checks["offline-manifest:coverage"] is True


# --- 构建环境与 Tauri 配置一致性 -------------------------------------------


def test_requirements_pin_the_cv2_provider_used_at_build_time():
    lines = [line.strip() for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    pins = {line.split("==")[0]: line.split("==")[1] for line in lines if "==" in line}

    # 两个发行版同时提供 cv2 时，安装顺序决定实际版本，构建不可复现。
    assert "opencv-python-headless" not in pins
    assert "opencv-contrib-python" in pins
    spec = (ROOT / "KongguWorker.spec").read_text(encoding="utf-8")
    assert "copy_metadata" in spec
    assert '"opencv-contrib-python"' in spec

    import cv2
    import importlib.metadata

    assert importlib.metadata.version("opencv-contrib-python") == pins["opencv-contrib-python"]
    assert cv2.__version__ == "4.10.0"


def test_prepare_and_release_gate_share_manifest_source_directories():
    assert prepare.OFFLINE_SOURCE_DIRS == OFFLINE_SOURCE_DIRS
    assert release_gate.OFFLINE_SOURCE_DIRS == OFFLINE_SOURCE_DIRS
    assert OFFLINE_SOURCE_DIRS == ("config", "assets", "resources/ocr_models")


def test_sidecar_spawn_capability_matches_external_binary():
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    capabilities = json.loads(
        (ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8")
    )
    permissions = capabilities["permissions"]
    spawn = next(
        permission
        for permission in permissions
        if isinstance(permission, dict) and permission.get("identifier") == "shell:allow-spawn"
    )

    assert tauri["bundle"]["externalBin"] == [entry["name"] for entry in spawn["allow"]]
    assert spawn["allow"][0]["args"] == ["--serve"]
    assert "fs:default" not in permissions
    assert "shell:allow-execute" not in permissions
    sidecar_source = (ROOT / "app" / "sidecar.py").read_text(encoding="utf-8")
    assert '"--serve"' in sidecar_source


def test_offline_manifest_matches_shared_source_directories():
    manifest = json.loads((ROOT / "resources" / "offline_manifest.json").read_text(encoding="utf-8"))
    manifest_sources = {str(entry["source"]).replace("\\", "/") for entry in manifest["entries"]}
    bundled_files: set[str] = set()
    for source in OFFLINE_SOURCE_DIRS:
        path = ROOT / source
        if path.is_dir():
            bundled_files.update(item.relative_to(ROOT).as_posix() for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            bundled_files.add(source)

    assert manifest_sources == bundled_files

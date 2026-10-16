from pathlib import Path

import app.offline_resources as offline_resources
from app.offline_resources import ResourcePaths, _target_path, build_manifest, repair_resources, resolve_resource_paths, resource_status


def test_resource_repair_copies_bundled_files_and_reports_ready_config(tmp_path: Path, monkeypatch):
    bundled = tmp_path / "bundled"
    app_data = tmp_path / "appdata"
    config = bundled / "config"
    config.mkdir(parents=True)
    (config / "period_time.json").write_text('{"periods":[]}', encoding="utf-8")

    for model in ("PP-OCRv4_mobile_det", "PP-OCRv4_mobile_rec"):
        model_dir = bundled / "resources" / "ocr_models" / model
        model_dir.mkdir(parents=True)
        (model_dir / "inference.yml").write_text("model: fake", encoding="utf-8")

    manifest = build_manifest(bundled, ["config", "resources/ocr_models"])
    manifest_path = bundled / "resources" / "offline_manifest.json"
    manifest_path.write_text(__import__("json").dumps(manifest, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("KONGGU_BUNDLED_RESOURCE_ROOT", str(bundled))
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))
    monkeypatch.setattr(
        offline_resources,
        "_ocr_runtime_status",
        lambda: {"ready": True, "stage": "ready", "error": ""},
    )

    before = resource_status()
    repaired = repair_resources()
    after = resource_status()

    assert before["ready"] is False
    assert repaired["ok"] is True
    assert after["ready"] is True
    assert (app_data / "resources" / "config" / "period_time.json").exists()
    assert (app_data / "ocr_models" / "PP-OCRv4_mobile_det" / "inference.yml").exists()

    repaired_again = repair_resources()
    assert repaired_again["ok"] is True
    assert repaired_again["copied"] == []
    assert repaired_again["status"]["ready"] is True


def test_resource_status_reports_hash_mismatch(tmp_path: Path, monkeypatch):
    bundled = tmp_path / "bundled"
    app_data = tmp_path / "appdata"
    config = bundled / "config"
    config.mkdir(parents=True)
    (config / "period_time.json").write_text("expected", encoding="utf-8")
    manifest = build_manifest(bundled, ["config"])
    (bundled / "resources").mkdir()
    (bundled / "resources" / "offline_manifest.json").write_text(
        __import__("json").dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    target = app_data / "resources" / "config"
    target.mkdir(parents=True)
    (target / "period_time.json").write_text("changed", encoding="utf-8")

    monkeypatch.setenv("KONGGU_BUNDLED_RESOURCE_ROOT", str(bundled))
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))

    status = resource_status()

    assert status["ready"] is False
    assert status["invalid"][0]["issue"] == "size-mismatch"


def test_ocr_models_use_ascii_programdata_root_for_non_ascii_app_data(tmp_path: Path, monkeypatch):
    app_data = tmp_path / "用户目录" / "Konggu"
    program_data = tmp_path / "ProgramData"
    monkeypatch.setattr(offline_resources, "_requires_ascii_path", lambda: True)
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.delenv("PUBLIC", raising=False)
    monkeypatch.delenv("KONGGU_OCR_MODEL_DIR", raising=False)

    paths = resolve_resource_paths()

    assert paths.app_data_root == app_data
    assert paths.ocr_models_root == program_data / "Konggu" / "ocr_models"


def test_repair_mirrors_ocr_models_to_ascii_runtime_root(tmp_path: Path, monkeypatch):
    bundled = tmp_path / "bundled"
    app_data = tmp_path / "用户目录" / "Konggu"
    program_data = tmp_path / "ProgramData"
    for model in ("PP-OCRv4_mobile_det", "PP-OCRv4_mobile_rec"):
        model_dir = bundled / "resources" / "ocr_models" / model
        model_dir.mkdir(parents=True)
        (model_dir / "inference.yml").write_text("model: fake", encoding="utf-8")

    manifest = build_manifest(bundled, ["resources/ocr_models"])
    manifest_path = bundled / "resources" / "offline_manifest.json"
    manifest_path.write_text(__import__("json").dumps(manifest, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(offline_resources, "_requires_ascii_path", lambda: True)
    monkeypatch.setattr(
        offline_resources,
        "_ocr_runtime_status",
        lambda: {"ready": True, "stage": "ready", "error": ""},
    )
    monkeypatch.setenv("KONGGU_BUNDLED_RESOURCE_ROOT", str(bundled))
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.delenv("PUBLIC", raising=False)
    monkeypatch.delenv("KONGGU_OCR_MODEL_DIR", raising=False)

    repaired = repair_resources()

    assert repaired["ok"] is True
    assert repaired["status"]["ready"] is True
    assert (program_data / "Konggu" / "ocr_models" / "PP-OCRv4_mobile_det" / "inference.yml").exists()
    assert not (app_data / "ocr_models" / "PP-OCRv4_mobile_det" / "inference.yml").exists()


def test_ocr_status_reports_path_incompatibility_when_no_ascii_root_is_available(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(offline_resources, "_requires_ascii_path", lambda: True)

    status = offline_resources._ocr_status(tmp_path / "用户目录" / "ocr_models")

    assert status["ready"] is False
    assert status["issue"] == "path-incompatible"
    assert "非 ASCII" in status["error"]


def test_explicit_ascii_ocr_model_dir_is_honored(tmp_path: Path, monkeypatch):
    app_data = tmp_path / "用户目录" / "Konggu"
    explicit_root = tmp_path / "Models" / "Konggu" / "ocr_models"
    monkeypatch.setattr(offline_resources, "_requires_ascii_path", lambda: True)
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(app_data))
    monkeypatch.setenv("KONGGU_OCR_MODEL_DIR", str(explicit_root))

    paths = resolve_resource_paths()

    assert paths.ocr_models_root == explicit_root


def test_manifest_paths_cannot_escape_resource_roots(tmp_path: Path):
    paths = ResourcePaths(
        bundled_root=tmp_path / "bundled",
        app_data_root=tmp_path / "appdata",
        user_resources_root=tmp_path / "appdata" / "resources",
        ocr_models_root=tmp_path / "appdata" / "ocr_models",
        cache_root=tmp_path / "appdata" / "cache",
        logs_root=tmp_path / "appdata" / "logs",
    )

    for entry in (
        {"target": "../outside.txt"},
        {"target": "C:/outside.txt"},
        {"target": "\\\\server\\share\\outside.txt"},
        {"target": "resources/../../outside.txt"},
    ):
        try:
            _target_path(paths, entry)
        except ValueError:
            continue
        raise AssertionError(f"Manifest path should have been rejected: {entry}")

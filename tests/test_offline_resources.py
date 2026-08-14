from pathlib import Path

from app.offline_resources import ResourcePaths, _target_path, build_manifest, repair_resources, resource_status


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

    before = resource_status()
    repaired = repair_resources()
    after = resource_status()

    assert before["ready"] is False
    assert repaired["ok"] is True
    assert after["ready"] is True
    assert (app_data / "resources" / "config" / "period_time.json").exists()
    assert (app_data / "ocr_models" / "PP-OCRv4_mobile_det" / "inference.yml").exists()


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

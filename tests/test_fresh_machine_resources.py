"""Fresh-machine resource setup: synthetic bundles, no developer models or network."""
from __future__ import annotations

import builtins
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from app import bootstrap
from app import offline_resources as resources


def _clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(('KONGGU_', 'PADDLE')) or name == 'MPLCONFIGDIR':
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(resources, '_requires_ascii_path', lambda: True)


def _deny_creating_files(monkeypatch, denied: Path):
    original = os.open
    def guarded(path, flags, *args, **kwargs):
        if flags & os.O_CREAT and Path(path).resolve().is_relative_to(denied.resolve()):
            raise PermissionError('synthetic standard-user write denial')
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', guarded)


def _bundle(root: Path):
    (root / 'config').mkdir(parents=True)
    (root / 'config' / 'reference_library.json').write_text('{"samples": []}', encoding='utf-8')
    for model in resources.REQUIRED_OCR_MODELS:
        folder = root / 'resources' / 'ocr_models' / model
        folder.mkdir(parents=True)
        (folder / 'inference.yml').write_text('model: synthetic', encoding='utf-8')
        (folder / 'inference.json').write_text('{}', encoding='utf-8')
        (folder / 'inference.pdiparams').write_bytes(b'synthetic-weights')
    manifest = resources.build_manifest(root, ['config', 'resources/ocr_models'])
    (root / 'resources' / resources.MANIFEST_NAME).write_text(json.dumps(manifest), encoding='utf-8')


def test_existing_read_only_programdata_is_not_selected_as_a_writable_model_root(tmp_path, monkeypatch):
    _clean_environment(monkeypatch)
    program = tmp_path / 'ProgramData'
    (program / 'Konggu' / 'ocr_models').mkdir(parents=True)
    public = tmp_path / 'Public'
    monkeypatch.setenv('KONGGU_APP_DATA_ROOT', str(tmp_path / '用户' / 'Konggu'))
    monkeypatch.setenv('PROGRAMDATA', str(program))
    monkeypatch.setenv('PUBLIC', str(public))
    _deny_creating_files(monkeypatch, program)

    assert resources.resolve_resource_paths().ocr_models_root == public / 'Konggu' / 'ocr_models'


def test_unwritable_explicit_model_root_does_not_stick_after_permission_change(tmp_path, monkeypatch):
    _clean_environment(monkeypatch)
    denied = tmp_path / 'OldModels'
    denied.mkdir()
    public = tmp_path / 'Public'
    monkeypatch.setenv('KONGGU_APP_DATA_ROOT', str(tmp_path / '用户' / 'Konggu'))
    monkeypatch.setenv('KONGGU_OCR_MODEL_DIR', str(denied))
    monkeypatch.delenv('PROGRAMDATA', raising=False)
    monkeypatch.setenv('PUBLIC', str(public))
    _deny_creating_files(monkeypatch, denied)

    paths = resources.configure_offline_environment()
    assert paths.ocr_models_root == public / 'Konggu' / 'ocr_models'
    assert Path(os.environ['KONGGU_OCR_MODEL_DIR']) == paths.ocr_models_root


def test_frozen_bootstrap_does_not_load_developer_cache_setup(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    original = builtins.__import__
    def no_developer_setup(name, *args, **kwargs):
        if name == 'sitecustomize':
            raise AssertionError('developer setup writes next to its source file')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_developer_setup)
    spec = importlib.util.spec_from_file_location('app._fresh_frozen_bootstrap', bootstrap.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def test_frozen_bundle_repairs_with_chinese_profile_and_standard_user_permissions(tmp_path, monkeypatch):
    _clean_environment(monkeypatch)
    bundle = tmp_path / 'InstalledBundle'
    _bundle(bundle)
    program = tmp_path / 'ProgramData'
    (program / 'Konggu' / 'ocr_models').mkdir(parents=True)
    public = tmp_path / 'Public'
    unrelated = tmp_path / 'unrelated-working-directory'
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / '用户' / 'Local'))
    monkeypatch.setenv('PROGRAMDATA', str(program))
    monkeypatch.setenv('PUBLIC', str(public))
    monkeypatch.setattr(resources, '_ocr_runtime_status', lambda: {'ready': True, 'stage': 'ready'})
    _deny_creating_files(monkeypatch, program)

    bootstrap.configure_runtime_environment()
    result = resources.repair_resources()

    assert result['status']['ready'] is True
    assert not result['failed']
    assert Path(os.environ['KONGGU_OCR_MODEL_DIR']) == public / 'Konggu' / 'ocr_models'
    assert bootstrap.resource_path('config') == tmp_path / '用户' / 'Local' / 'Konggu' / 'resources' / 'config'
    assert (Path(os.environ['KONGGU_OCR_MODEL_DIR']) / resources.REQUIRED_OCR_MODELS[0] / 'inference.pdiparams').read_bytes() == b'synthetic-weights'
    assert not (unrelated / 'resources').exists()


def test_frozen_missing_core_error_points_to_complete_package_not_developer_source(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bootstrap, 'configure_runtime_environment', lambda: None)
    original = builtins.__import__
    def missing_core(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'core' and 'schedule_core' in fromlist:
            raise ImportError('synthetic missing bundled dependency')
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', missing_core)

    with pytest.raises(RuntimeError) as exc:
        bootstrap.load_schedule_core()
    assert '离线安装包' in str(exc.value)
    assert 'core/schedule_core.py' not in str(exc.value)


def test_missing_manifest_error_gives_offline_reinstallation_action(tmp_path, monkeypatch):
    _clean_environment(monkeypatch)
    monkeypatch.setenv('KONGGU_BUNDLED_RESOURCE_ROOT', str(tmp_path / 'incomplete-bundle'))
    monkeypatch.setenv('KONGGU_APP_DATA_ROOT', str(tmp_path / 'appdata'))
    status = resources.resource_status()
    assert status['ready'] is False
    assert status['manifest_issue'] == 'missing-manifest'
    assert '完整离线安装包' in status['manifest_error']

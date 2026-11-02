"""Release checks inspect the worker payload, not just its executable name."""
import hashlib
import json
from pathlib import Path

import pytest

from tools import verify_release_bundle as release_gate
from tools.worker_bundle import runtime_datas, worker_build_metadata


def _release_root(root):
    from tests.test_audit_platform import _write_release_versions

    _write_release_versions(root, '0.6.0')
    package = json.loads((root/'package.json').read_text())
    package['dependencies'] = {name: '1' for name in [
        'pdfjs-dist', '@fullcalendar/core', '@fullcalendar/timegrid',
        '@fullcalendar/interaction', '@tauri-apps/plugin-fs',
    ]}
    (root/'package.json').write_text(json.dumps(package))
    (root/'src-tauri/Cargo.toml').write_text(
        '[package]\nname="konggu"\nversion="0.6.0"\n'
        '[dependencies]\ntauri-plugin-fs="2"\ntauri-plugin-shell="2"\n')
    capabilities = root/'src-tauri/capabilities/default.json'
    capabilities.parent.mkdir()
    capabilities.write_text(json.dumps(dict(permissions=[
        'shell:allow-spawn', 'shell:allow-stdin-write', 'shell:allow-kill', 'fs:allow-read-file',
    ])))
    (root/'dist/assets').mkdir(parents=True)
    (root/'dist/assets/pdf.worker.js').write_text('worker')
    worker = root/'src-tauri/binaries/konggu-worker-x86_64-pc-windows-msvc.exe'
    worker.parent.mkdir()
    worker.write_bytes(b'MZ-not-a-PyInstaller-worker')
    entries = []
    for index in range(6):
        relative = f'resources/ocr_models/model/{index}.bin'
        source = root/relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'model')
        entries.append(dict(source=relative, target=f'ocr_models/model/{index}.bin',
                            size=5, sha256=hashlib.sha256(b'model').hexdigest(), kind='ocr_model'))
    (root/'resources/offline_manifest.json').write_text(json.dumps(dict(entries=entries)))
    truth = root/'benchmarks/ground_truth/manifest.json'
    truth.parent.mkdir(parents=True)
    truth.write_text('{}')
    return root


def test_release_gate_rejects_a_file_named_sidecar_without_a_worker_archive(tmp_path, monkeypatch, capsys):
    root = _release_root(tmp_path)
    monkeypatch.setattr(release_gate, 'ROOT', root)
    monkeypatch.setattr('sys.argv', ['verify_release_bundle.py'])
    assert release_gate.main() == 1
    assert 'FAIL\tsidecar:archive' in capsys.readouterr().out


def test_worker_runtime_selection_excludes_reference_snapshots(tmp_path):
    root = _release_root(tmp_path)
    reference = root/'resources/reference/private-notes.txt'
    reference.parent.mkdir()
    reference.write_text('reference-only')
    selected = {Path(source).relative_to(root).as_posix() for source, _ in runtime_datas(root)}
    assert len(selected) == 7
    assert 'resources/offline_manifest.json' in selected
    assert not any('reference' in name for name in selected)


@pytest.mark.parametrize('problem', ['changed_model', 'reference_source', 'escape_source'])
def test_worker_build_refuses_corrupt_or_nonruntime_manifest_sources(tmp_path, problem):
    root = _release_root(tmp_path)
    manifest_path = root/'resources/offline_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if problem == 'changed_model':
        (root/manifest['entries'][0]['source']).write_bytes(b'changed')
    else:
        manifest['entries'][0]['source'] = ('resources/reference/note.txt'
            if problem == 'reference_source' else '../outside/model.bin')
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        runtime_datas(root)


def test_worker_provenance_tracks_source_changes_without_machine_paths(tmp_path):
    root = _release_root(tmp_path)
    for name in ['KongguWorker.spec', 'requirements.txt', 'sitecustomize.py', 'tools/worker_bundle.py', 'core/parser.py']:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original')
    first = worker_build_metadata(root)
    (root/'core/parser.py').write_text('changed parser')
    changed = worker_build_metadata(root)
    assert changed['source_fingerprint'] != first['source_fingerprint']
    assert changed['application_version'] == '0.6.0'
    assert str(root) not in json.dumps(changed)


def test_nsis_must_embed_the_same_worker_that_was_audited(tmp_path):
    candidate = tmp_path/'new-worker.exe'
    candidate.write_bytes(b'new-worker')
    old = tmp_path/'old-worker.exe'
    old.write_bytes(b'old-worker')
    script = tmp_path/'installer.nsi'
    script.write_text(f'File /a "/oname=konggu-worker.exe" "{old}"\n')
    assert release_gate._installer_worker_checks(script, candidate)[0][1] is False
    script.write_text(f'File /a "/oname=konggu-worker.exe" "{candidate}"\n')
    assert release_gate._installer_worker_checks(script, candidate)[0][1] is True


def test_webview_path_defines_cannot_pass_without_a_real_standalone_installer(tmp_path):
    script = tmp_path/'installer.nsi'
    source = tmp_path/'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    script.write_text(f'!define INSTALLWEBVIEW2MODE "offlineInstaller"\n'
                      f'!define WEBVIEW2INSTALLERPATH "{source}"\n'
                      'File "${WEBVIEW2INSTALLERPATH}"\n')
    checks = {name: passed for name, passed, _ in release_gate._webview2_checks(script)}
    assert checks['webview2:offline-mode']
    assert checks['webview2:standalone-installer'] is False


def test_dependency_audit_never_treats_an_installed_vc_runtime_as_a_windows_system_dll():
    imports = {'kernel32.dll', 'msvcrt.dll', 'api-ms-win-crt-runtime-l1-1-0.dll',
               'vcruntime140.dll', 'msvcp140_atomic_wait.dll', 'missing-engine.dll'}
    assert release_gate._non_system_dependencies(imports, {'vcruntime140.dll'}) == {
        'msvcp140_atomic_wait.dll', 'missing-engine.dll',
    }
    assert release_gate._is_vc_runtime('msvcp140_atomic_wait.dll')
    assert not release_gate._is_vc_runtime('msvcrt.dll')


@pytest.mark.parametrize('problem', ['wrong_worker', 'missing_ocr', 'inherited_python', 'nonzero_exit',
                                    'no_image_preview', 'accepted_image', 'missing_result_denial', 'none'])
def test_isolation_report_requires_all_stages_and_the_exact_worker(tmp_path, problem):
    worker = tmp_path/'worker.exe'
    worker.write_bytes(b'candidate')
    stages = ['resource_restore_and_inference_probe', 'text_pair_preview', 'excel_export',
              'actual_image_ocr', 'unreviewed_export_denied']
    report = dict(format_version=1, ok=True, exit_code=0,
                  worker_sha256=hashlib.sha256(b'candidate').hexdigest(),
                  environment=dict(fresh_profile=True, fresh_models=True, system_only_path=True,
                                   project_outside_working_directory=True, inherited_python=False),
                  steps=[dict(stage=stage, ok=True) for stage in stages])
    report['steps'].append(dict(stage='image_preview_state', ok=True, produced_preview=True,
                                quality_state='blocked', valid_result_ref=True))
    report['steps'][-2]['denial_reason'] = 'quality_gate'
    if problem == 'wrong_worker':
        report['worker_sha256'] = '0'*64
    elif problem == 'missing_ocr':
        report['steps'] = [step for step in report['steps'] if step['stage'] != 'actual_image_ocr']
    elif problem == 'inherited_python':
        report['environment']['inherited_python'] = True
    elif problem == 'nonzero_exit':
        report['exit_code'] = 1
    elif problem == 'no_image_preview':
        report['steps'][-1]['produced_preview'] = False
    elif problem == 'accepted_image':
        report['steps'][-1]['quality_state'] = 'accepted'
    elif problem == 'missing_result_denial':
        report['steps'][-2]['denial_reason'] = 'missing_result'
    path = tmp_path/'report.json'
    path.write_text(json.dumps(report))
    assert release_gate._isolation_report_checks(path, worker)[0][1] is (problem == 'none')


def test_sidecar_copy_accepts_explicit_paths_from_another_working_directory_and_preserves_old_artifacts(tmp_path):
    import subprocess
    import shutil

    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is unavailable.')
    script = Path(__file__).resolve().parents[1]/'scripts/rename-sidecar.mjs'
    source = tmp_path/'new-output.exe'
    source.write_bytes(b'candidate-one')
    target_dir = tmp_path/'new-candidate/binaries'
    command = [node, str(script), '--source', str(source), '--target-dir', str(target_dir),
               '--target-triple', 'x86_64-pc-windows-msvc']
    first = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    target = target_dir/'konggu-worker-x86_64-pc-windows-msvc.exe'
    assert target.read_bytes() == b'candidate-one'
    source.write_bytes(b'candidate-two')
    second = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert second.returncode != 0
    assert 'previous artifacts are preserved' in second.stderr
    assert target.read_bytes() == b'candidate-one'


def test_signing_configuration_preserves_offline_template_and_selects_the_candidate_worker(tmp_path):
    from copy import deepcopy
    from tools.compose_build_config import compose

    base = dict(bundle=dict(externalBin=['old-worker'], windows=dict(
        nsis=dict(template='local-template.nsi'), webviewInstallMode=dict(type='offlineInstaller'))))
    before = deepcopy(base)
    worker = tmp_path/'binaries/konggu-worker-x86_64-pc-windows-msvc.exe'
    worker.parent.mkdir()
    worker.write_bytes(b'candidate')
    signed = compose(base, 'test-thumbprint', 'https://timestamp.invalid', worker)
    assert base == before
    assert signed['bundle']['externalBin'] == ['binaries/konggu-worker']
    windows = signed['bundle']['windows']
    assert windows['nsis']['template'] == 'local-template.nsi'
    assert windows['webviewInstallMode']['type'] == 'offlineInstaller'
    assert windows['certificateThumbprint'] == 'test-thumbprint'

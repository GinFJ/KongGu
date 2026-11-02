"""Offline template preparation preserves stock semantics and avoids downloads."""
from copy import deepcopy
from pathlib import Path

import pytest

from tools.prepare_local_bundle_tools import extract_template, local_template, offline_config
from tools.verify_release_bundle import _nsis_install_scope_checks


def _stock():
    return ('Unicode true\r\n'
            '!define INSTALLMODE "{{install_mode}}"\r\n'
            '!define INSTALLWEBVIEW2MODE "{{install_webview2_mode}}"\r\n'
            '!define WEBVIEW2INSTALLERPATH "{{webview2_installer_path}}"\r\n'
            '!define WEBVIEW2INSTALLERARGS "{{webview2_installer_args}}"\r\n'
            'File "${WEBVIEW2INSTALLERPATH}"\r\n'
            'ExecWait "$6 ${WEBVIEW2INSTALLERARGS} /install" $1\r\n'
            'FunctionEnd\r\n')


def test_local_cli_template_extraction_does_not_include_trailing_binary_data():
    source = _stock()
    binary = b'prefix\0'+source.encode()+b'\xf1\x1c\xaf\x80\x01\0tail'
    assert extract_template(binary) == source
    with pytest.raises(ValueError):
        extract_template(binary+binary)
    with pytest.raises(ValueError):
        extract_template(binary.replace(b'FunctionEnd', b'Truncated'))


def test_offline_template_embeds_runtime_and_quotes_temp_paths_with_spaces():
    runtime = Path(r'C:\local tools\runtime$copy\WebView2.exe')
    template = local_template(_stock(), runtime)
    assert '!define INSTALLWEBVIEW2MODE "offlineInstaller"' in template
    assert '!define WEBVIEW2INSTALLERARGS "/silent"' in template
    assert 'runtime$$copy' in template
    assert 'ExecWait \'"$6" ${WEBVIEW2INSTALLERARGS} /install\' $1' in template
    assert '{{install_mode}}' in template  # The selected all-users mode stays authoritative.
    with pytest.raises(ValueError):
        local_template(template, runtime)


def test_skip_only_applies_to_bundler_and_preserves_all_users_installation(tmp_path):
    base = dict(bundle=dict(windows=dict(nsis=dict(installMode='perMachine'),
                                       certificateThumbprint='signing-choice')))
    before = deepcopy(base)
    config = offline_config(base, tmp_path/'offline.nsi', tmp_path/'binaries/konggu-worker-x86_64-pc-windows-msvc.exe')
    assert base == before
    assert config['bundle']['windows']['webviewInstallMode'] == dict(type='skip', silent=None)
    assert config['bundle']['windows']['nsis']['installMode'] == 'perMachine'
    assert config['bundle']['windows']['certificateThumbprint'] == 'signing-choice'
    assert config['bundle']['externalBin'] == ['binaries/konggu-worker']
    assert config['bundle']['useLocalToolsDir'] is True


@pytest.mark.parametrize('mode,allowed', [('perMachine', True), ('currentUser', False), ('both', False)])
def test_release_scope_follows_the_all_users_installation_request(tmp_path, mode, allowed):
    script = tmp_path/'installer.nsi'
    script.write_text(f'!define INSTALLMODE "{mode}"\n')
    assert _nsis_install_scope_checks(script)[0][1] is allowed


def test_native_tauri_merge_accepts_skip_without_inheriting_offline_silent(tmp_path):
    import json
    import os
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[1]
    cli = root/'node_modules/@tauri-apps/cli/tauri.js'
    node = shutil.which('node')
    if os.name != 'nt' or node is None or not cli.is_file():
        pytest.skip('The installed Windows Tauri CLI is required.')
    config = offline_config({}, tmp_path/'offline.nsi', tmp_path/'binaries/konggu-worker-x86_64-pc-windows-msvc.exe')
    # Stop after schema validation, before Vite, Rust compilation or bundling.
    config['build'] = dict(beforeBuildCommand='cmd /c exit 97')
    path = tmp_path/'schema-probe.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    environment = dict(os.environ, CARGO_NET_OFFLINE='true')
    result = subprocess.run([node, str(cli), 'build', '--config', str(path)], cwd=root,
        env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    output = result.stdout+result.stderr
    assert 'beforeBuildCommand' in output and '97' in output, output
    assert 'oneOf' not in output

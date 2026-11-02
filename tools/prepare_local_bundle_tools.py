"""Prepare an offline NSIS tool copy and a local-runtime Tauri template.

The stock template is extracted from the installed Tauri CLI of this checkout.
Nothing is downloaded and no existing candidate/cache is changed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def validate_prepared_tools(tools: Path) -> dict:
    manifest = json.loads((tools/'tools_manifest.json').read_text(encoding='utf-8'))
    files = {
        'template': tools/'offline-installer.nsi',
        'runtime': tools/'runtime/MicrosoftEdgeWebView2RuntimeInstallerX64.exe',
        'cli': ROOT/'node_modules/@tauri-apps/cli-win32-x64-msvc/cli.win32-x64-msvc.node',
    }
    for name, path in files.items():
        if not path.is_file() or digest(path) != manifest.get(name+'_sha256'):
            raise ValueError('Prepared '+name+' changed; prepare a new tools directory.')
    if files['runtime'].stat().st_size != manifest.get('runtime_bytes'):
        raise ValueError('Prepared runtime size changed.')
    for path in ['local-appdata/tauri/NSIS/Bin/makensis.exe',
                 'local-appdata/tauri/NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll']:
        if not (tools/path).is_file():
            raise ValueError('Prepared NSIS compiler or plugin is missing.')
    return manifest


def extract_template(data: bytes) -> str:
    marker = b'!define INSTALLWEBVIEW2MODE "{{install_webview2_mode}}"'
    if data.count(marker) != 1:
        raise ValueError('Installed CLI does not contain one recognizable NSIS template.')
    index = data.index(marker)
    start, boundary = data.rfind(b'\0', 0, index)+1, data.find(b'\0', index)
    ending = b'FunctionEnd\r\n'
    last = data.rfind(ending, start, boundary)
    if last < 0:
        ending = b'FunctionEnd\n'
        last = data.rfind(ending, start, boundary)
    end = last+len(ending)
    if (boundary < 0 or last < 0 or boundary-end > 16
            or not data[start:].startswith(b'Unicode true')):
        raise ValueError('Cannot prove the complete installed NSIS template boundaries.')
    return data[start:end].decode('utf-8')


def local_template(template: str, runtime: Path) -> str:
    replacements = {
        '!define INSTALLWEBVIEW2MODE "{{install_webview2_mode}}"': '!define INSTALLWEBVIEW2MODE "offlineInstaller"',
        '!define WEBVIEW2INSTALLERPATH "{{webview2_installer_path}}"':
            '!define WEBVIEW2INSTALLERPATH "'+str(runtime).replace('$', '$$')+'"',
        '!define WEBVIEW2INSTALLERARGS "{{webview2_installer_args}}"': '!define WEBVIEW2INSTALLERARGS "/silent"',
        'ExecWait "$6 ${WEBVIEW2INSTALLERARGS} /install" $1':
            'ExecWait \'"$6" ${WEBVIEW2INSTALLERARGS} /install\' $1',
    }
    for before, after in replacements.items():
        if template.count(before) != 1:
            raise ValueError('Installed NSIS template structure changed; review before patching.')
        template = template.replace(before, after)
    return template


def offline_config(base: dict, template: Path, worker: Path) -> dict:
    result = deepcopy(base)
    windows = result.setdefault('bundle', {}).setdefault('windows', {})
    # Tauri recursively merge-patches this object with tauri.conf.json. Null
    # removes the offlineInstaller-only field; omitting it retains silent=true
    # and makes the final `skip` enum variant fail the installed CLI's schema.
    windows['webviewInstallMode'] = dict(type='skip', silent=None)
    windows.setdefault('nsis', {})['template'] = template.as_posix()
    # Runtime shell registration must equal Command.sidecar and its capability.
    # The audited candidate is staged into this generated path before compilation.
    result['bundle']['externalBin'] = ['binaries/konggu-worker']
    result['bundle']['useLocalToolsDir'] = True
    return result


def prepare(output: Path, worker: Path, old_script: Path, base: dict | None = None) -> dict:
    output, worker = output.resolve(), worker.resolve(strict=True)
    if output.exists():
        raise ValueError('Choose a new tools directory; existing caches and candidates are preserved.')
    text = old_script.read_text(encoding='utf-8')
    match = re.search(r'^!define WEBVIEW2INSTALLERPATH "([^\"]+)"', text, re.M)
    if match is None:
        raise ValueError('The previous generated NSIS script has no local Standalone Runtime path.')
    runtime = Path(match[1])
    cache = next((parent for parent in runtime.parents if parent.name.lower() == 'tauri'), None)
    if cache is None or not (cache/'NSIS/Bin/makensis.exe').is_file():
        raise ValueError('The verified previous NSIS cache is unavailable.')
    if (not runtime.is_file() or runtime.stat().st_size < 10*1024*1024
            or not (cache/'NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll').is_file()):
        raise ValueError('The previous local NSIS plugin or Standalone Runtime is missing.')
    import pefile
    with pefile.PE(str(runtime), fast_load=True):
        pass
    cli = ROOT/'node_modules/@tauri-apps/cli-win32-x64-msvc/cli.win32-x64-msvc.node'
    template = extract_template(cli.read_bytes())
    output.mkdir(parents=True)
    destination_cache = output/'local-appdata/tauri'
    shutil.copytree(cache/'NSIS', destination_cache/'NSIS')
    copied_runtime = output/'runtime/MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    copied_runtime.parent.mkdir()
    shutil.copy2(runtime, copied_runtime)
    template_path = output/'offline-installer.nsi'
    template_path.write_text(local_template(template, copied_runtime), encoding='utf-8')
    config = offline_config(base or {}, template_path, worker)
    (output/'tauri-offline.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    if digest(runtime) != digest(copied_runtime):
        raise ValueError('The Standalone Runtime copy failed its source hash check.')
    report = dict(format_version=1, downloaded=False, original_cache_changed=False,
                  nsis_file_count=sum(1 for p in (destination_cache/'NSIS').rglob('*') if p.is_file()),
                  runtime_bytes=copied_runtime.stat().st_size, runtime_sha256=digest(copied_runtime),
                  cli_sha256=digest(cli), template_sha256=digest(template_path),
                  bundler_webview_mode='skip', actual_installer_webview_mode='offlineInstaller')
    (output/'tools_manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path)
    mode.add_argument('--reuse-tools', type=Path)
    mode.add_argument('--validate-tools', type=Path)
    parser.add_argument('--config-output', type=Path)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--source-installer-script', type=Path, default=ROOT/'src-tauri/target/release/nsis/x64/installer.nsi')
    parser.add_argument('--base-config', type=Path)
    args = parser.parse_args()
    base = json.loads(args.base_config.read_text(encoding='utf-8-sig')) if args.base_config else {}
    if args.validate_tools:
        validate_prepared_tools(args.validate_tools.resolve(strict=True))
        print('Prepared offline compiler/template/runtime hashes match.')
        return
    if args.reuse_tools:
        tools = args.reuse_tools.resolve(strict=True)
        if args.config_output is None:
            parser.error('--reuse-tools requires --config-output')
        validate_prepared_tools(tools)
        template = tools/'offline-installer.nsi'
        config = offline_config(base, template, args.worker.resolve(strict=True))
        with args.config_output.open('x', encoding='utf-8') as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        print('Prepared tools reused; candidate worker binding refreshed.')
        return
    print(json.dumps(prepare(args.output, args.worker, args.source_installer_script, base), indent=2))


if __name__ == '__main__':
    main()

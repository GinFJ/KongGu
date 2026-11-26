"""Static release audit for the offline Tauri/sidecar bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.offline_resources import OFFLINE_SOURCE_DIRS, _manifest_relative_path  # noqa: E402
from tools.worker_bundle import BUILD_METADATA, sha256_file, worker_build_metadata  # noqa: E402


_CARGO_VERSION = re.compile(r'(?m)^\s*version\s*=\s*"([^"]+)"')
_CARGO_PACKAGE_SECTION = re.compile(r"(?m)^\[package\]\s*$")


def _cargo_package_version(text: str) -> str:
    match = _CARGO_PACKAGE_SECTION.search(text)
    if match is None:
        return "<missing>"
    version = _CARGO_VERSION.search(text[match.end():])
    return version.group(1) if version else "<missing>"


def _cargo_lock_version(text: str, package_name: str) -> str:
    for block in text.split("[[package]]"):
        if re.search(rf'(?m)^name = "{re.escape(package_name)}"\s*$', block):
            version = re.search(r'(?m)^version = "([^"]+)"\s*$', block)
            return version.group(1) if version else "<missing>"
    return "<missing>"


def _collect_versions(root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}

    def json_version(relative: str, *, nested: tuple[str, ...] = ()) -> str:
        path = root / relative
        if not path.is_file():
            return "<missing>"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in nested:
            if not isinstance(payload, dict):
                return "<missing>"
            payload = payload.get(key) or {}
        if not isinstance(payload, dict):
            return "<missing>"
        return str(payload.get("version") or "<missing>")

    versions["package.json"] = json_version("package.json")
    versions["package-lock.json"] = json_version("package-lock.json")
    versions["package-lock.json:packages"] = json_version("package-lock.json", nested=("packages", ""))
    versions["src-tauri/tauri.conf.json"] = json_version("src-tauri/tauri.conf.json")
    cargo_toml = root / "src-tauri" / "Cargo.toml"
    versions["src-tauri/Cargo.toml"] = (
        _cargo_package_version(cargo_toml.read_text(encoding="utf-8")) if cargo_toml.is_file() else "<missing>"
    )
    cargo_lock = root / "src-tauri" / "Cargo.lock"
    versions["src-tauri/Cargo.lock"] = (
        _cargo_lock_version(cargo_lock.read_text(encoding="utf-8"), "konggu") if cargo_lock.is_file() else "<missing>"
    )
    return versions


def _version_checks(root: Path) -> list[tuple[str, bool, str]]:
    """The release checklist requires one application version across every manifest."""

    versions = _collect_versions(root)
    expected = versions.get("package.json", "<missing>")
    return [
        (
            f"version:{name}",
            expected != "<missing>" and version == expected,
            f"{version} (package.json {expected})",
        )
        for name, version in versions.items()
    ]


def _normalize_manifest_source(value: object) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _manifest_path_checks(manifest: dict) -> list[tuple[str, bool, str]]:
    entries = [entry for entry in manifest.get("entries") or [] if isinstance(entry, dict)]
    problems: list[str] = []
    for entry in entries:
        for field in ("source", "target"):
            try:
                _manifest_relative_path(entry.get(field), field)
            except ValueError:
                problems.append(f"{field}:{entry.get(field)!r}")
    return [
        (
            "offline-manifest:paths",
            bool(entries) and not problems,
            ", ".join(problems[:3]) if problems else f"{len(entries)} entries",
        )
    ]


def _bundled_source_files(root: Path) -> set[str]:
    files: set[str] = set()
    for source in OFFLINE_SOURCE_DIRS:
        path = root / source
        if path.is_file():
            files.add(source)
        elif path.is_dir():
            files.update(item.relative_to(root).as_posix() for item in path.rglob("*") if item.is_file())
    return files


def _manifest_coverage_checks(root: Path, manifest: dict) -> list[tuple[str, bool, str]]:
    """Every bundled config/asset/model file must be described by the manifest."""

    entries = [entry for entry in manifest.get("entries") or [] if isinstance(entry, dict)]
    declared = {_normalize_manifest_source(entry.get("source")) for entry in entries}
    present = _bundled_source_files(root)
    unlisted = sorted(present - declared)
    missing = sorted(declared - present)
    detail = f"{len(declared)} sources"
    problems: list[str] = []
    if unlisted:
        problems.append("unlisted:" + ", ".join(unlisted[:3]))
    if missing:
        problems.append("missing:" + ", ".join(missing[:3]))
    if problems:
        detail = "; ".join(problems)
    return [
        (
            "offline-manifest:coverage",
            bool(declared) and not unlisted and not missing,
            detail,
        )
    ]


_WINDOWS_SYSTEM_DLLS = {
    'advapi32.dll', 'bcrypt.dll', 'bcryptprimitives.dll', 'combase.dll', 'comctl32.dll',
    'comdlg32.dll', 'crypt32.dll', 'd3d11.dll', 'dbghelp.dll', 'dwmapi.dll', 'dxgi.dll',
    'gdi32.dll', 'iphlpapi.dll', 'kernel32.dll', 'mf.dll', 'mfplat.dll', 'mfreadwrite.dll',
    'msvcrt.dll', 'ntdll.dll', 'ole32.dll', 'oleaut32.dll', 'pdh.dll', 'powrprof.dll',
    'propsys.dll', 'psapi.dll', 'rpcrt4.dll', 'secur32.dll', 'shell32.dll', 'shlwapi.dll',
    'user32.dll', 'version.dll', 'ws2_32.dll', 'wsock32.dll',
}


def _pe_imports(data: bytes) -> set[str]:
    import pefile

    with pefile.PE(data=data, fast_load=True) as image:
        image.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT'],
        ])
        imports = [*getattr(image, 'DIRECTORY_ENTRY_IMPORT', []),
                   *getattr(image, 'DIRECTORY_ENTRY_DELAY_IMPORT', [])]
        return {item.dll.decode('ascii').lower() for item in imports}


def _is_vc_runtime(name: str) -> bool:
    return bool(re.fullmatch(
        r'(?:vcruntime\d+(?:_\d+)*|msvcp\d+(?:_\w+)*|concrt\d+|vcomp\d+|msvcr\d+)\.dll',
        name, flags=re.I))


def _vc_dependencies(data: bytes) -> set[str]:
    """VC++ redistributable imports; Windows 10/11 system CRT is not bundled."""
    return {name for name in _pe_imports(data) if _is_vc_runtime(name)}


def _non_system_dependencies(imports: set[str], available: set[str]) -> set[str]:
    # Do not use this developer machine's System32 inventory: an installed
    # redistributable there could conceal a dependency missing on a clean PC.
    return {name for name in imports-available if name not in _WINDOWS_SYSTEM_DLLS
            and not name.startswith(('api-ms-win-', 'ext-ms-'))}


def _worker_archive_checks(root: Path, worker: Path) -> list[tuple[str, bool, str]]:
    try:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(worker))
    except Exception:
        return [('sidecar:archive', False, 'missing or invalid PyInstaller payload')]
    names = {name.replace('\\', '/'): name for name in archive.toc}
    checks = [('sidecar:archive', True, f'{len(names)} entries')]
    try:
        metadata = json.loads(archive.extract(names[BUILD_METADATA]))
        matches = metadata == worker_build_metadata(root)
    except (KeyError, ValueError, TypeError, OSError):
        matches = False
    checks.append(('sidecar:source-fingerprint', matches, 'current source and application version'))
    unexpected = sorted(name for name in names if name.startswith((
        'resources/reference/', 'docs/', 'tests/', 'benchmarks/', 'tmp/', 'cache/', '.venv/',
    )))
    checks.append(('sidecar:runtime-only', not unexpected,
                   ', '.join(unexpected[:3]) if unexpected else 'no development/reference directories'))
    manifest = json.loads((root/'resources/offline_manifest.json').read_text(encoding='utf-8'))
    payload_errors = []
    for entry in manifest.get('entries', []):
        source = str(entry.get('source') or '').replace('\\', '/')
        try:
            data = archive.extract(names[source])
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                payload_errors.append(source)
        except (KeyError, TypeError, ValueError):
            payload_errors.append(source)
    bundled_manifest = names.get('resources/offline_manifest.json')
    if bundled_manifest is None or archive.extract(bundled_manifest) != (root/'resources/offline_manifest.json').read_bytes():
        payload_errors.append('resources/offline_manifest.json')
    checks.append(('sidecar:offline-payload', not payload_errors and bool(manifest.get('entries')),
                   ', '.join(payload_errors[:3]) if payload_errors else 'all declared runtime bytes match'))
    try:
        modules = archive.open_embedded_archive('PYZ.pyz').toc
        required = {'core.schedule_core', 'core.bordered_timetables', 'core.vector_notice_consensus',
                    'core.visual_notice_consensus', 'core.identity_completion',
                    'core.english_split_course', 'core.ocr_notice_refinement',
                    'core.ocr_location_refinement', 'app.services.review_operations',
                    'app.bootstrap', 'app.offline_resources'}
        missing_modules = sorted(required - modules.keys())
        checks.append(('sidecar:core-modules', not missing_modules,
                       ', '.join(missing_modules) if missing_modules else 'parser and bootstrap present'))
        available = {Path(name).name.lower() for name in names}
        missing_vc = set()
        external = set()
        for name in names:
            if name.lower().endswith(('.dll', '.pyd', '.exe')):
                imports = _pe_imports(archive.extract(names[name]))
                external.update(_non_system_dependencies(imports, available))
                missing_vc.update(name for name in imports-available if _is_vc_runtime(name))
        imports = _pe_imports(worker.read_bytes())
        external.update(_non_system_dependencies(imports, available))
        missing_vc.update(name for name in imports-available if _is_vc_runtime(name))
        checks.append(('sidecar:vc-runtime', not missing_vc,
                       ', '.join(sorted(missing_vc)) if missing_vc else 'all recursive VC++ imports bundled'))
        checks.append(('sidecar:external-runtime', not external,
                       ', '.join(sorted(external)) if external else 'only bundled DLLs or Windows 10/11 system APIs'))
    except Exception:
        checks.append(('sidecar:dependency-audit', False, 'unable to inspect modules or PE imports'))
    return checks


def _webview2_checks(installer_script: Path) -> list[tuple[str, bool, str]]:
    text = installer_script.read_text(encoding='utf-8') if installer_script.is_file() else ''
    runtime_match = re.search(r'^!define WEBVIEW2INSTALLERPATH "([^\"]+)"', text, re.M)
    runtime = Path(runtime_match[1]) if runtime_match else None
    valid_runtime = False
    if runtime is not None and runtime.is_file() and runtime.stat().st_size >= 10*1024*1024:
        try:
            _vc_dependencies(runtime.read_bytes())  # Require a real PE, not a placeholder.
            valid_runtime = True
        except Exception:
            pass
    embedded = bool(re.search(r'(?m)^\s*File\b[^\n]*\$\{WEBVIEW2INSTALLERPATH\}', text))
    return [
        ('webview2:offline-mode', '!define INSTALLWEBVIEW2MODE "offlineInstaller"' in text, 'installer.nsi'),
        ('webview2:standalone-installer', valid_runtime and embedded,
         f'{runtime.name} {sha256_file(runtime)}' if valid_runtime and runtime is not None else 'missing/invalid standalone runtime'),
    ]


def _installer_worker_checks(installer_script: Path, worker: Path) -> list[tuple[str, bool, str]]:
    text = installer_script.read_text(encoding='utf-8') if installer_script.is_file() else ''
    sources = []
    for match in re.finditer(r'(?m)^\s*File\b([^\n]+)', text):
        quoted = re.findall(r'"([^\"]+)"', match[1])
        if '/oname=konggu-worker.exe' in quoted and quoted:
            sources.append(Path(quoted[-1]))
    correct = (len(sources) == 1 and sources[0].is_file() and worker.is_file()
               and sha256_file(sources[0]) == sha256_file(worker))
    return [('nsis:worker-source', correct, 'NSIS input matches the audited worker hash')]


def _nsis_install_scope_checks(installer_script: Path) -> list[tuple[str, bool, str]]:
    text = installer_script.read_text(encoding='utf-8') if installer_script.is_file() else ''
    all_users = bool(re.search(r'^!define INSTALLMODE "perMachine"\s*$', text, re.M))
    return [('nsis:all-users', all_users, 'all-users installation with normal administrator UAC')]


def _isolation_report_checks(report_path: Path, worker: Path) -> list[tuple[str, bool, str]]:
    required = {'resource_restore_and_inference_probe', 'text_pair_preview', 'text_pair_course_verification', 'excel_export',
                'actual_image_ocr', 'unreviewed_export_denied'}
    try:
        report = json.loads(report_path.read_text(encoding='utf-8'))
        completed = {step.get('stage') for step in report.get('steps', []) if step.get('ok') is True}
        preview = next((step for step in report.get('steps', []) if step.get('stage') == 'image_preview_state'), {})
        denied = next((step for step in report.get('steps', []) if step.get('stage') == 'unreviewed_export_denied'), {})
        environment = report.get('environment') or {}
        isolated = (report.get('format_version') == 1 and report.get('ok') is True
            and report.get('exit_code') == 0 and report.get('worker_sha256') == sha256_file(worker)
            and required <= completed and environment.get('inherited_python') is False
            and preview.get('produced_preview') is True and preview.get('valid_result_ref') is True
            and preview.get('quality_state') in {'blocked', 'needs_review'}
            and denied.get('denial_reason') == 'quality_gate'
            and all(environment.get(name) is True for name in (
                'fresh_profile', 'fresh_models', 'system_only_path', 'project_outside_working_directory')))
    except (OSError, ValueError, TypeError, AttributeError):
        isolated = False
    return [('sidecar:isolated-runtime', isolated, 'fresh profile, real OCR and export report for this worker')]


def _registration_checks(root: Path, overlay: Path | None = None) -> list[tuple[str, bool, str]]:
    try:
        config = json.loads((root/'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
        binaries = config.get('bundle', {}).get('externalBin', [])
        if overlay:
            overrides = json.loads(overlay.read_text(encoding='utf-8-sig'))
            binaries = overrides.get('bundle', {}).get('externalBin', binaries)
        frontend = (root/'src/sidecarClient.ts').read_text(encoding='utf-8')
        names = set(re.findall(r'Command\.sidecar\(["\']([^"\']+)["\']', frontend))
        capabilities = json.loads((root/'src-tauri/capabilities/default.json').read_text(encoding='utf-8'))
        allowed = {entry.get('name') for permission in capabilities.get('permissions', [])
                   if isinstance(permission, dict) and permission.get('identifier') == 'shell:allow-spawn'
                   for entry in permission.get('allow', [])
                   if entry.get('sidecar') is True and entry.get('args') == ['--serve']}
        matches = bool(names) and names == set(binaries or []) == allowed
    except (OSError, ValueError, TypeError, AttributeError):
        matches = False
    return [('sidecar:runtime-registration', matches,
             'frontend / effective bundle / capability must agree exactly')]


def _pdfjs_asset_checks(root: Path) -> list[tuple[str, bool, str]]:
    checks = []
    for directory in ('cmaps', 'standard_fonts'):
        originals = list((root/'node_modules/pdfjs-dist'/directory).glob('*'))
        mismatches = []
        for source in originals:
            target = root/'dist/pdfjs'/directory/source.name
            if not target.is_file() or hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                mismatches.append(source.name)
        checks.append((f'frontend:pdf-{directory}', bool(originals) and not mismatches,
                       f'{len(originals)} local resources' if not mismatches else ', '.join(mismatches[:3])))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-installer", action="store_true")
    parser.add_argument('--worker', type=Path, help='Audit an explicitly selected worker candidate.')
    parser.add_argument('--target-dir', type=Path, help='Cargo output directory used for this candidate.')
    parser.add_argument('--isolation-report', type=Path, help='Require a successful run bound to this exact worker hash.')
    parser.add_argument('--tauri-config', type=Path, default=os.environ.get('KONGGU_TAURI_CONFIG') or None,
                        help='Verify the effective build overlay against the runtime sidecar registration.')
    parser.add_argument('--skip-frontend-artifact', action='store_true', help='Worker-build preflight before Vite runs; not a desktop release acceptance.')
    parser.add_argument('--source-only', action='store_true', help='Validate source manifests before building; does not inspect any executable.')
    args = parser.parse_args()
    if args.source_only and (args.require_installer or args.isolation_report):
        parser.error('--source-only cannot accept an installer or runtime report')
    checks: list[tuple[str, bool, str]] = []

    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    dependencies = package.get("dependencies", {})
    for name in (
        "pdfjs-dist",
        "@fullcalendar/core",
        "@fullcalendar/timegrid",
        "@fullcalendar/interaction",
        "@tauri-apps/plugin-fs",
    ):
        checks.append((f"dependency:{name}", name in dependencies, str(dependencies.get(name, ""))))

    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    # The current desktop shell uses the fs and shell plugins only. The old
    # persisted-scope plugin was removed when file-path persistence moved into
    # the sidecar session store; keeping it in this gate makes every current
    # release fail despite a valid Cargo manifest.
    for name in ("tauri-plugin-fs", "tauri-plugin-shell"):
        checks.append((f"cargo:{name}", name in cargo, name))

    permissions = json.loads(
        (ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8")
    )
    encoded_permissions = json.dumps(permissions, ensure_ascii=False)
    for name in ("shell:allow-spawn", "shell:allow-stdin-write", "shell:allow-kill", "fs:allow-read-file"):
        checks.append((f"permission:{name}", name in encoded_permissions, name))
    checks.extend(_registration_checks(ROOT, args.tauri_config))

    frontend_assets = list((ROOT / "dist" / "assets").glob("*")) if (ROOT / "dist" / "assets").exists() else []
    if not args.skip_frontend_artifact and not args.source_only:
        checks.append(("frontend:pdf-worker", any("pdf.worker" in item.name for item in frontend_assets), "dist/assets"))
        checks.extend(_pdfjs_asset_checks(ROOT))
    worker = args.worker or ROOT / 'src-tauri/binaries/konggu-worker-x86_64-pc-windows-msvc.exe'
    if not args.source_only:
        checks.append(("sidecar", worker.is_file(), "binary"))
        checks.extend(_worker_archive_checks(ROOT, worker))
    if args.isolation_report:
        checks.extend(_isolation_report_checks(args.isolation_report, worker))

    manifest = json.loads((ROOT / "resources" / "offline_manifest.json").read_text(encoding="utf-8"))
    manifest_entries = list(manifest.get("entries", []))
    model_entries = [entry for entry in manifest.get("entries", []) if entry.get("kind") == "ocr_model"]
    checks.append(("offline-manifest:ocr", len(model_entries) >= 6, f"{len(model_entries)} files"))
    invalid_manifest_entries: list[str] = []
    for entry in manifest_entries:
        source_value = str(entry.get("source") or "")
        try:
            relative_source = _manifest_relative_path(source_value, "source")
        except ValueError:
            invalid_manifest_entries.append(f"path:{source_value}")
            continue
        source = ROOT / relative_source
        if not source.is_file():
            invalid_manifest_entries.append(f"missing:{source_value}")
            continue
        try:
            expected_size = int(entry["size"])
        except (KeyError, TypeError, ValueError):
            invalid_manifest_entries.append(f"size-invalid:{source_value}")
            continue
        if source.stat().st_size != expected_size:
            invalid_manifest_entries.append(f"size:{source_value}")
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != str(entry.get("sha256") or "").strip().lower():
            invalid_manifest_entries.append(f"hash:{source_value}")
    checks.append(
        (
            "offline-manifest:integrity",
            bool(manifest_entries) and not invalid_manifest_entries,
            f"{len(manifest_entries)} entries" if not invalid_manifest_entries else ", ".join(invalid_manifest_entries[:3]),
        )
    )
    checks.extend(_version_checks(ROOT))
    checks.extend(_manifest_path_checks(manifest))
    checks.extend(_manifest_coverage_checks(ROOT, manifest))
    tauri = json.loads((ROOT/'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
    mode = tauri.get('bundle', {}).get('windows', {}).get('nsis', {}).get('installMode')
    checks.append(('windows:install-scope', mode == 'perMachine', str(mode or 'missing')))
    checks.append(("truth-manifest", (ROOT / "benchmarks" / "ground_truth" / "manifest.json").exists(), "12 samples"))

    target_dir = args.target_dir or ROOT / 'src-tauri/target'
    expected_installer = (
        target_dir
        / "release"
        / "bundle"
        / "nsis"
        / f"Konggu_{package.get('version')}_x64-setup.exe"
    )
    if args.require_installer:
        installer_script = target_dir / 'release/nsis/x64/installer.nsi'
        checks.append(
            (
                "nsis-installer",
                expected_installer.is_file(),
                expected_installer.name if expected_installer.is_file() else "missing",
            )
        )
        checks.extend(_webview2_checks(installer_script))
        checks.extend(_installer_worker_checks(installer_script, worker))
        checks.extend(_nsis_install_scope_checks(installer_script))
        main_binary = target_dir/'release/konggu.exe'
        try:
            vc = _vc_dependencies(main_binary.read_bytes())
            absent = sorted(name for name in vc if not (main_binary.parent/name).is_file())
            checks.append(('desktop:vc-runtime', not absent,
                           ', '.join(absent) if absent else 'Windows 10/11 system APIs or adjacent VC++ runtime'))
        except Exception:
            checks.append(('desktop:vc-runtime', False, 'main binary missing or invalid PE'))

    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}\t{name}\t{detail}")
    return 0 if all(passed for _, passed, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

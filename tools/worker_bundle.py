"""Build-only selection and provenance for the self-contained Windows worker."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


BUILD_METADATA = 'resources/worker_build.json'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_datas(root: Path) -> list[tuple[str, str]]:
    """Select only hash-verified runtime files, excluding the reference library."""
    root = root.resolve()
    manifest_path = root/'resources/offline_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    entries = manifest.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Offline manifest has no runtime files.')
    selected = {}
    for entry in entries:
        value = str(entry.get('source') or '').replace('\\', '/')
        relative = PurePosixPath(value)
        if (relative.is_absolute() or ':' in value or '..' in relative.parts
                or not value.startswith(('config/', 'assets/', 'resources/ocr_models/'))):
            raise ValueError('Offline manifest contains a non-runtime source.')
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f'Offline runtime file is missing: {relative}')
        if (path.stat().st_size != entry.get('size')
                or sha256_file(path) != str(entry.get('sha256') or '').lower()):
            raise ValueError(f'Offline runtime file failed integrity: {relative}')
        selected[relative.as_posix()] = (str(path), relative.parent.as_posix())
    return [selected[key] for key in sorted(selected)] + [(str(manifest_path), 'resources')]


def worker_build_metadata(root: Path) -> dict:
    """No machine paths or personal data enter the executable provenance."""
    root = root.resolve()
    paths = [root/name for name in [
        'KongguWorker.spec', 'requirements.txt', 'sitecustomize.py', 'tools/worker_bundle.py',
    ]]
    for directory in ['app', 'core']:
        paths.extend(sorted((root/directory).rglob('*.py')))
    paths.append(root/'resources/offline_manifest.json')
    files = {path.relative_to(root).as_posix(): sha256_file(path) for path in paths}
    version = str(json.loads((root/'package.json').read_text(encoding='utf-8'))['version'])
    payload = dict(format_version=1, application_version=version, source_files=files)
    payload['source_fingerprint'] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return payload


def write_worker_build_metadata(root: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory/'worker_build.json'
    output.write_text(json.dumps(worker_build_metadata(root), indent=2, sort_keys=True), encoding='utf-8')
    return output

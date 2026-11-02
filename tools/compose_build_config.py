"""Preserve candidate/NSIS overrides when adding Tauri signing configuration."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path


def compose(base: dict, certificate: str, timestamp: str, worker: Path | None = None) -> dict:
    result = deepcopy(base)
    if not isinstance(result, dict):
        raise ValueError('Tauri build config must be a JSON object.')
    bundle = result.setdefault('bundle', {})
    if not isinstance(bundle, dict):
        raise ValueError('Tauri bundle overrides must be a JSON object.')
    windows = bundle.setdefault('windows', {})
    if not isinstance(windows, dict):
        raise ValueError('Tauri Windows overrides must be a JSON object.')
    windows.update(certificateThumbprint=certificate, digestAlgorithm='sha256', timestampUrl=timestamp, tsp=False)
    if worker is not None:
        worker = worker.resolve(strict=True)
        if not worker.name.startswith('konggu-worker-') or worker.suffix.lower() != '.exe':
            raise ValueError('Select the copied worker carrying its Rust target triple.')
        bundle['externalBin'] = ['binaries/konggu-worker']
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--certificate', required=True)
    parser.add_argument('--timestamp', required=True)
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding='utf-8-sig')) if args.base else {}
    config = compose(base, args.certificate, args.timestamp, args.worker)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()

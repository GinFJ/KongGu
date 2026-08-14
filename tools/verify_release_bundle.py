"""Static release audit for the offline Tauri/sidecar bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-installer", action="store_true")
    args = parser.parse_args()
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

    frontend_assets = list((ROOT / "dist" / "assets").glob("*")) if (ROOT / "dist" / "assets").exists() else []
    checks.append(("frontend:pdf-worker", any("pdf.worker" in item.name for item in frontend_assets), "dist/assets"))
    checks.append(("sidecar", (ROOT / "src-tauri" / "binaries" / "konggu-worker-x86_64-pc-windows-msvc.exe").exists(), "binary"))

    manifest = json.loads((ROOT / "resources" / "offline_manifest.json").read_text(encoding="utf-8"))
    manifest_entries = list(manifest.get("entries", []))
    model_entries = [entry for entry in manifest.get("entries", []) if entry.get("kind") == "ocr_model"]
    checks.append(("offline-manifest:ocr", len(model_entries) >= 6, f"{len(model_entries)} files"))
    invalid_manifest_entries: list[str] = []
    for entry in manifest_entries:
        source = ROOT / str(entry.get("source") or "")
        if not source.is_file():
            invalid_manifest_entries.append(f"missing:{entry.get('source')}")
            continue
        if source.stat().st_size != int(entry.get("size") or -1):
            invalid_manifest_entries.append(f"size:{entry.get('source')}")
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != str(entry.get("sha256") or ""):
            invalid_manifest_entries.append(f"hash:{entry.get('source')}")
    checks.append(
        (
            "offline-manifest:integrity",
            bool(manifest_entries) and not invalid_manifest_entries,
            f"{len(manifest_entries)} entries" if not invalid_manifest_entries else ", ".join(invalid_manifest_entries[:3]),
        )
    )
    checks.append(("truth-manifest", (ROOT / "benchmarks" / "ground_truth" / "manifest.json").exists(), "12 samples"))

    expected_installer = (
        ROOT
        / "src-tauri"
        / "target"
        / "release"
        / "bundle"
        / "nsis"
        / f"Konggu_{package.get('version')}_x64-setup.exe"
    )
    if args.require_installer:
        checks.append(
            (
                "nsis-installer",
                expected_installer.is_file(),
                expected_installer.name if expected_installer.is_file() else "missing",
            )
        )

    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}\t{name}\t{detail}")
    return 0 if all(passed for _, passed, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Static release audit for the offline Tauri/sidecar bundle."""

from __future__ import annotations

import argparse
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
    for name in ("tauri-plugin-fs", "tauri-plugin-persisted-scope", "tauri-plugin-shell"):
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
    model_entries = [entry for entry in manifest.get("entries", []) if entry.get("kind") == "ocr_model"]
    checks.append(("offline-manifest:ocr", len(model_entries) >= 6, f"{len(model_entries)} files"))
    checks.append(("truth-manifest", (ROOT / "benchmarks" / "ground_truth" / "manifest.json").exists(), "12 samples"))

    installers = list((ROOT / "src-tauri" / "target" / "release" / "bundle" / "nsis").glob("*.exe"))
    if args.require_installer:
        checks.append(("nsis-installer", bool(installers), installers[-1].name if installers else "missing"))

    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}\t{name}\t{detail}")
    return 0 if all(passed for _, passed, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Static release audit for the offline Tauri/sidecar bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.offline_resources import OFFLINE_SOURCE_DIRS, _manifest_relative_path  # noqa: E402


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
        installer_script = ROOT / "src-tauri" / "target" / "release" / "nsis" / "x64" / "installer.nsi"
        installer_script_text = installer_script.read_text(encoding="utf-8") if installer_script.is_file() else ""
        checks.append(
            (
                "nsis-installer",
                expected_installer.is_file(),
                expected_installer.name if expected_installer.is_file() else "missing",
            )
        )
        checks.append(
            (
                "webview2:offline-mode",
                '!define INSTALLWEBVIEW2MODE "offlineInstaller"' in installer_script_text,
                "installer.nsi",
            )
        )
        checks.append(
            (
                "webview2:standalone-installer",
                '!define WEBVIEW2INSTALLERPATH "' in installer_script_text,
                "embedded runtime path",
            )
        )

    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}\t{name}\t{detail}")
    return 0 if all(passed for _, passed, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

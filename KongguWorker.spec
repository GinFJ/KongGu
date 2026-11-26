# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import json
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(ROOT))
from tools.worker_bundle import runtime_datas, write_worker_build_metadata

datas = runtime_datas(ROOT)
# Explicit private build input. Never store the credential in tracked resources.
credential_file = os.environ.get("KONGGU_AI_CREDENTIAL_FILE", "")
if credential_file:
    credential_path = Path(credential_file).resolve(strict=True)
    credential_data = json.loads(credential_path.read_text(encoding="utf-8"))
    if not str(credential_data.get("api_key", "")).strip():
        raise ValueError("Private AI credential is missing")
    if credential_path.name != "ai-credential.json":
        raise ValueError("Private AI credential must use the documented file name")
    datas.append((str(credential_path), "private"))
build_metadata = write_worker_build_metadata(ROOT, Path(workpath) / "provenance")
datas.append((str(build_metadata), "resources"))
datas += collect_data_files("paddlex", includes=["configs/**/*.yaml", "configs/**/*.yml"])
datas += collect_data_files("paddleocr", includes=["**/*.yaml", "**/*.yml"])
datas += copy_metadata("pdf-inspector")
for metadata_package in [
    "paddlex",
    "paddleocr",
    "imagesize",
    "opencv-contrib-python",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "shapely",
]:
    datas += copy_metadata(metadata_package)

binaries = collect_dynamic_libs("paddle")
binaries += collect_dynamic_libs("pdf_inspector")

a = Analysis(
    [str(ROOT / "app" / "sidecar.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "paddleocr",
        "paddle",
        "cv2",
        "fitz",
        "pdf_inspector",
        "openpyxl",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "sphinx",
        "IPython",
        "notebook",
        "jupyter",
        "jupyter_client",
        "jupyter_core",
        "nbformat",
        "matplotlib",
        "scipy",
        "pyarrow",
        "numba",
        "llvmlite",
        "boto3",
        "botocore",
        "black",
        "yapf",
        "PyQt5",
        "qtpy",
        "tkinter",
        "tables",
        "sqlalchemy",
        "dask",
        "distributed",
        "bokeh",
        "h5py",
        "zmq",
        "keyring",
        "skimage",
        "sklearn",
        "nltk",
        "tensorflow",
        "conda",
        "rich",
        "jsonschema",
        "argon2",
        "anyio",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="konggu-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

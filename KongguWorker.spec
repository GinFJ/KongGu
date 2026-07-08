# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path.cwd()
datas = [
    (str(ROOT / "config"), "config"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "resources"), "resources"),
]
datas += collect_data_files("paddlex", includes=["configs/**/*.yaml", "configs/**/*.yml"])
datas += collect_data_files("paddleocr", includes=["**/*.yaml", "**/*.yml"])
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

a = Analysis(
    ["app/sidecar.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "paddleocr",
        "paddle",
        "cv2",
        "fitz",
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

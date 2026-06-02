# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)

datas = [
    (str(project_root / "config"), "config"),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "cache"), "cache"),
]

hiddenimports = [
    "fitz",
    "cv2",
    "numpy",
    "openpyxl",
    "paddle",
    "paddleocr",
]

excludes = [
    "black",
    "bokeh",
    "botocore",
    "Crypto",
    "baidubce",
    "dask",
    "distributed",
    "future",
    "gradio",
    "h5py",
    "huggingface_hub",
    "httpx",
    "IPython",
    "ipykernel",
    "altair",
    "astropy",
    "astropy_iers_data",
    "intake",
    "jedi",
    "jupyter",
    "jupyter_client",
    "jupyter_core",
    "jupyterlab",
    "llvmlite",
    "matplotlib",
    "modelscope",
    "nbformat",
    "nbconvert",
    "nltk",
    "notebook",
    "numba",
    "panel",
    "paramiko",
    "patsy",
    "plotly",
    "prettytable",
    "pypdfium2",
    "pyviz_comms",
    "pyarrow",
    "PyQt5",
    "PyQt6",
    "PySide6",
    "pytest",
    "qtpy",
    "rich",
    "safetensors",
    "scipy",
    "shapely",
    "skimage",
    "sklearn",
    "sphinx",
    "sqlalchemy",
    "statsmodels",
    "tables",
    "tensorflow",
    "torch",
    "tokenizers",
    "transformers",
    "xarray",
    "zmq",
]


a = Analysis(
    ["desktop_clean.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Konggu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Konggu",
)

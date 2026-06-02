# Konggu V0.1

Konggu is a Windows desktop tool for importing Chinese and English schedule PDFs, parsing class blocks, and producing member availability summaries.

## Features

- Tkinter desktop entry point in `desktop_clean.py`.
- PDF text extraction and OCR-assisted parsing in `core/schedule_core.py`.
- Structured schedule, file record, and processing result models in `core/models.py`.
- Configurable period times, school calendar, reference library metadata, and OCR model URLs under `config/`.
- PyInstaller packaging recipe in `Konggu.spec`.

## Setup

Use Python 3.12, then install the runtime dependencies:

```powershell
python -m pip install -r requirements.txt
```

OCR model files are intentionally not committed. Download them locally when OCR support is needed:

```powershell
download_ocr_models.bat
```

## Run

```powershell
python desktop_clean.py
```

## Test

```powershell
python -m pytest -q
```

## Build

```powershell
pyinstaller Konggu.spec
```

Generated folders such as `build/`, `dist/`, `cache/`, `__pycache__/`, logs, and export artifacts are ignored by Git.

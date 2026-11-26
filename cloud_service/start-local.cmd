@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
  python -m venv ".venv" || goto failed
)
".venv\Scripts\python.exe" -c "from importlib.metadata import version; from pathlib import Path; pins = [s.split('==') for s in Path('requirements.txt').read_text().splitlines() if s.strip()]; assert all(version(name) == wanted for name, wanted in pins)" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
)
if not defined KONGGU_CLOUD_DATA set "KONGGU_CLOUD_DATA=%LOCALAPPDATA%\KongguCloudService"
echo Konggu account service: http://127.0.0.1:8765
echo Keep this window open while using local accounts. Press Ctrl+C to stop.
".venv\Scripts\python.exe" server.py --port 8765
if errorlevel 1 goto failed
exit /b 0
:failed
echo Check Python 3.11 or 3.12, the service directory and the error above.
pause
exit /b 1

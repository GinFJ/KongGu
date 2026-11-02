@echo off
setlocal
cd /d "%~dp0.."
if errorlevel 1 exit /b 1

if not defined KONGGU_WORKER_CANDIDATE (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-candidate.ps1"
  if errorlevel 1 exit /b 1
  exit /b 0
)

call npm.cmd run check:privacy
if errorlevel 1 exit /b 1

set "VSDEVCMD=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
if exist "%VSDEVCMD%" goto build
echo Visual Studio Build Tools not found: %VSDEVCMD%
exit /b 1

:build
call "%VSDEVCMD%" -arch=x64
if errorlevel 1 exit /b %errorlevel%

set "LOCAL_NSIS=%CD%\tools\nsis\nsis-3.11"
if exist "%LOCAL_NSIS%\Bin\makensis.exe" (
  set "PATH=%LOCAL_NSIS%\Bin;%LOCAL_NSIS%;%PATH%"
)

set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

if defined KONGGU_WORKER_CANDIDATE (
  if not defined KONGGU_TAURI_CONFIG (
    echo Candidate worker requires KONGGU_TAURI_CONFIG with its externalBin path.
    exit /b 1
  )
)

"%CD%\.venv\Scripts\python.exe" "%CD%\tools\verify_release_bundle.py" --worker "%KONGGU_WORKER_CANDIDATE%" --skip-frontend-artifact
if errorlevel 1 exit /b 1

if defined KONGGU_SIGN_CERT_THUMBPRINT (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\sign-windows-artifact.ps1" -Path "%KONGGU_WORKER_CANDIDATE%"
  if errorlevel 1 exit /b 1
)

"%CD%\.venv\Scripts\python.exe" "%CD%\tools\stage_worker_candidate.py" --candidate "%KONGGU_WORKER_CANDIDATE%"
if errorlevel 1 exit /b 1

if defined KONGGU_TAURI_CONFIG (
  call npm.cmd exec tauri build -- --config "%KONGGU_TAURI_CONFIG%"
) else (
  call npm.cmd exec tauri build
)
if errorlevel 1 exit /b 1

:verify
if not defined CARGO_TARGET_DIR set "CARGO_TARGET_DIR=%CD%\src-tauri\target"
if defined KONGGU_WORKER_ISOLATION_REPORT (
  "%CD%\.venv\Scripts\python.exe" "%CD%\tools\verify_release_bundle.py" --require-installer --worker "%KONGGU_WORKER_CANDIDATE%" --target-dir "%CARGO_TARGET_DIR%" --isolation-report "%KONGGU_WORKER_ISOLATION_REPORT%"
) else (
  "%CD%\.venv\Scripts\python.exe" "%CD%\tools\verify_release_bundle.py" --require-installer --worker "%KONGGU_WORKER_CANDIDATE%" --target-dir "%CARGO_TARGET_DIR%"
)
exit /b %errorlevel%

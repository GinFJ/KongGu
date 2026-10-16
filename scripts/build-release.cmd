@echo off
setlocal

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

if defined KONGGU_SIGN_CERT_THUMBPRINT (
  npm.cmd run build:sidecar
  if errorlevel 1 exit /b 1

  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\sign-windows-artifact.ps1" -Path "%CD%\src-tauri\binaries\konggu-worker-x86_64-pc-windows-msvc.exe"
  if errorlevel 1 exit /b 1

  if defined KONGGU_TAURI_CONFIG (
    npm.cmd exec tauri build -- --config "%KONGGU_TAURI_CONFIG%"
  ) else (
    npm.cmd exec tauri build
  )
  if errorlevel 1 exit /b 1
  exit /b 0
)

if defined KONGGU_TAURI_CONFIG (
  npm.cmd run build -- --config "%KONGGU_TAURI_CONFIG%"
) else (
  npm.cmd run build
)

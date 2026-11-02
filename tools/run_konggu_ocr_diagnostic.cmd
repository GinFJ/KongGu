@echo off
setlocal EnableExtensions
title Konggu Offline Diagnostic
set "diagDir=%TEMP%\KongguDiagnostics"
if not exist "%diagDir%" mkdir "%diagDir%"
set "bootLog=%diagDir%\launch-%RANDOM%-%RANDOM%.txt"
>"%bootLog%" echo STARTED - Konggu diagnostic launcher
echo Konggu offline check is starting. No Python installation is required.
echo Keep this window open until the result appears.
if not exist "%~dp0diagnose_konggu_ocr.ps1" (
    >>"%bootLog%" echo FAILED - The PS1 helper is missing. Extract the full ZIP first.
    type "%bootLog%"
    pause
    exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose_konggu_ocr.ps1" -OutputDirectory "%diagDir%" -NoPause >>"%bootLog%" 2>&1
set "diagExit=%ERRORLEVEL%"
type "%bootLog%"
echo.
echo Exit code: %diagExit%
echo Reports: %diagDir%
echo If it failed, keep the report and send it to the project maintainer.
pause
exit /b %diagExit%

@echo off
setlocal EnableExtensions
title Konggu OCR Diagnostic

cd /d "%~dp0"
echo.
echo ========================================
echo   Konggu OCR diagnostic is starting...
echo ========================================
echo.

if not exist "%~dp0diagnose_konggu_ocr.ps1" (
    echo ERROR: diagnose_konggu_ocr.ps1 was not found in this folder.
    echo Please put this CMD file and the PS1 file in the same folder.
    echo.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose_konggu_ocr.ps1"
set "exitCode=%ERRORLEVEL%"

echo.
if exist "%USERPROFILE%\Desktop\Konggu_OCR_Diagnostic_Result.txt" (
    echo Result file: %USERPROFILE%\Desktop\Konggu_OCR_Diagnostic_Result.txt
) else (
    echo WARNING: the result file was not found on the desktop.
)
echo PowerShell exit code: %exitCode%
echo.
pause
exit /b %exitCode%

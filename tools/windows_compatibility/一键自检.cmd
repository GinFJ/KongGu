@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-selfcheck.ps1" %*
set "SELF_CHECK_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %SELF_CHECK_RESULT%

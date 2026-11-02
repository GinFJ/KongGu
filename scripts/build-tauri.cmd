@echo off
setlocal
rem Every desktop build must use the same fresh-worker and release gates.
call "%~dp0build-release.cmd"
exit /b %errorlevel%

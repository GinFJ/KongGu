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
npm.cmd run build

@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_installer.ps1" %*
exit /b %errorlevel%

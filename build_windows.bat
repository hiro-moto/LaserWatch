@echo off
setlocal
call "%~dp0build_installer.bat" %*
exit /b %errorlevel%

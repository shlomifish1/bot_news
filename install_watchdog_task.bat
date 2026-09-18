@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_watchdog_task.ps1"
exit /b %ERRORLEVEL%

@echo off
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Bot News Aggregator

if exist "venv\Scripts\pythonw.exe" (
    "venv\Scripts\pythonw.exe" main.py
    exit /b %ERRORLEVEL%
)
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" main.py
) else (
    python main.py
)

set "EXIT_CODE=%ERRORLEVEL%"
exit /b %EXIT_CODE%

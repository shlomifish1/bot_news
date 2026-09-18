@echo off
setlocal
title Bot News Aggregator
cd /d "%~dp0"

echo ============================================
echo  Bot News - start
echo ============================================

if not exist "venv\Scripts\python.exe" (
    echo Creating venv with Python 3.12...
    py -3.12 -m venv venv
    if errorlevel 1 (
        echo.
        echo ERROR: Python 3.12 was not found.
        echo Open CMD and run: python --version
        pause
        exit /b 1
    )

    echo Updating pip...
    "venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
)

if not exist "requirements.txt" (
    echo ERROR: requirements.txt was not found.
    pause
    exit /b 1
)

set "REQ_HASH="
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile requirements.txt SHA256') do if not defined REQ_HASH set "REQ_HASH=%%H"
set "HASH_FILE=venv\.requirements.sha256"
set "OLD_REQ_HASH="
if exist "%HASH_FILE%" set /p OLD_REQ_HASH=<"%HASH_FILE%"

if "%REQ_HASH%"=="%OLD_REQ_HASH%" (
    echo Packages are already installed.
) else (
    echo Installing/updating packages...
    "venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: package installation failed.
        pause
        exit /b 1
    )
    echo %REQ_HASH%>"%HASH_FILE%"
)

echo.
echo ============================================
echo  Starting bot...
echo ============================================
"venv\Scripts\python.exe" main.py
pause

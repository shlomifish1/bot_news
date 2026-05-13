@echo off
title Bot News Aggregator
cd /d "%~dp0"

echo ============================================
echo  Bot News - הגדרה ראשונית
echo ============================================

REM מחק את ה-venv הישן מהשרת הקודם
if exist venv (
    echo 🗑️  מוחק venv ישן...
    rmdir /s /q venv
)

echo 🔧 יוצר venv חדש עם Python 3.12...
py -3.12 -m venv venv
if errorlevel 1 (
    echo.
    echo ❌ שגיאה: Python לא נמצא!
    echo    פתח CMD והקלד: python --version
    echo    כדי לבדוק שPython מותקן כראוי.
    pause
    exit /b 1
)

echo ⚙️ מעדכן pip...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet

echo ⚙️ מתקין חבילות...
venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo ❌ שגיאה בהתקנה!
    pause
    exit /b 1
)

echo.
echo ============================================
echo 🚀 מפעיל את הבוט...
echo ============================================
venv\Scripts\python.exe main.py
pause

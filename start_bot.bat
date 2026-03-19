@echo off
title Bot News Aggregator
cd /d "%~dp0"
echo ⚙️ המערכת בודקת דרישות...
pip install -r requirements.txt
echo 🚀 מפעיל את הבוט...
python main.py
pause

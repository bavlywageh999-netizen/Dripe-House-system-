@echo off
cd /d "%~dp0"
echo Starting the shop system. Keep this window open while you work.
python app.py || py app.py
pause

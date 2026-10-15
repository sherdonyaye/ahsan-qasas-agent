@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run START-TELEGRAM.cmd once to install dependencies first.
 pause
 exit /b 1
)
.venv\Scripts\python.exe github_bridge.py
pause

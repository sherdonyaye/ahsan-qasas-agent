@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe telegram_bot.py
) else (
  py -3.12 -m venv .venv
  if errorlevel 1 goto fail
  .venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
  if errorlevel 1 goto fail
  .venv\Scripts\python.exe telegram_bot.py
)
pause
exit /b
:fail
echo Setup failed. Keep this window open and share the error, not your token.
pause

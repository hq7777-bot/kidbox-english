@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m kidbox.main --text
) else (
  python -m kidbox.main --text
)
echo.
pause

@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m kidbox.doctor %*
) else (
  python -m kidbox.doctor %*
)
echo.
pause

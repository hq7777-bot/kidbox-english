@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\package_release.py
) else (
  python tools\package_release.py
)
echo.
pause

@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m kidbox.main --script demo_script.txt --no-tts --step 1.5
) else (
  python -m kidbox.main --script demo_script.txt --no-tts --step 1.5
)
echo.
pause

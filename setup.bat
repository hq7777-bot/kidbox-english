@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  kidbox setup
echo ============================================

set "PY=python"
where py >nul 2>nul
if %errorlevel%==0 set "PY=py -3"

echo [1/5] Python:
%PY% --version
if errorlevel 1 (
  echo.
  echo [!] Python not found. Install Python 3.10+ from python.org
  echo     and check "Add python.exe to PATH".
  pause
  exit /b 1
)

echo [2/5] Create virtualenv .venv
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo [!] venv creation failed.
  pause
  exit /b 1
)

set "VPY=.venv\Scripts\python.exe"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

echo [3/5] Install core dependencies
"%VPY%" -m pip install -q -U pip
"%VPY%" -m pip install -q -r requirements-core.txt -i %MIRROR%
if errorlevel 1 (
  echo     mirror failed, retry with official PyPI ...
  "%VPY%" -m pip install -q -r requirements-core.txt
)
"%VPY%" -c "import requests" >nul 2>nul
if errorlevel 1 (
  echo [!] core dependencies still missing. Check network / proxy.
  pause
  exit /b 1
)

if /i "%1"=="--full" (
  echo [3b] Install audio dependencies ^(microphone + wake word^)
  "%VPY%" -m pip install -q -r requirements-audio.txt -i %MIRROR%
  if errorlevel 1 "%VPY%" -m pip install -q -r requirements-audio.txt
)

echo [4/5] Build demo audio library ^(skips existing files^)
"%VPY%" tools\make_demo_library.py

echo [5/5] Environment check
"%VPY%" -m kidbox.doctor

echo.
echo Done. Next:
echo    start_text.bat     - talk to it by typing
echo    start_script.bat   - run demo_script.txt unattended
echo    doctor.bat         - re-check environment
pause

@echo off
chcp 65001 >nul
set MSYSTEM=
set IDF_TOOLS_PATH=G:\esp\tools
set IDF_PATH=G:\esp\esp-idf
set IDF_PYTHON_ENV_PATH=G:\esp\tools\python_env\idf6.1_py3.13_env
set IDF_GITHUB_ASSETS=dl.espressif.cn/github_assets
call G:\esp\esp-idf\export.bat
where idf.py
if errorlevel 1 (echo [X] idf.py not in PATH & exit /b 1)
cd /d G:\refs\xiaozhi-esp32
idf.py --version
python scripts\build.py espressif/esp-vocat --name esp-vocat-xinxin --language zh-CN
echo BUILD_EXIT=%ERRORLEVEL%

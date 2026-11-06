@echo off
rem 一台闲置笔记本同时当"设备"和"大脑"（先跑通闭环用）
rem 双击运行。声音从这台笔记本的喇叭出，麦克风用它自带的。
chcp 65001 >nul
cd /d "%~dp0"

rem ---- 配置 ----
set KIDBOX_LIBRARY=%~dp0library
set KIDBOX_STATE=%~dp0progress.json
set KIDBOX_DEVICE=http://127.0.0.1:8788
set KIDBOX_PUBLIC=http://127.0.0.1:8787
set KIDBOX_BRAIN=deepseek
rem 想听云端的音色就改成 edge（慢 2 秒）
set KIDBOX_TTS=sapi
rem 第一次先设 1，只打日志不出声，确认指令对了再改成 0
set KIDBOX_FAKE=0
rem -----------------

echo [1/3] 启动设备端 :8788
start "kidbox-device" cmd /k python device_agent.py

timeout /t 2 >nul
echo [2/3] 启动大脑 :8787
start "kidbox-brain" cmd /k python pc_brain.py

timeout /t 3 >nul
echo [3/3] 启动语音循环（回车说话）
python voice_loop.py --pc http://127.0.0.1:8787 --device http://127.0.0.1:8788

pause

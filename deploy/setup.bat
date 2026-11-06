@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   小智服务端 · 一键准备（Windows）
echo ============================================
echo.

echo [1/6] 检查 Docker
docker version >nul 2>&1
if errorlevel 1 (
    echo    [X] 没检测到 Docker
    echo.
    echo    请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/
    echo    安装时务必勾选 "Use WSL 2 instead of Hyper-V"，装完重启电脑。
    echo.
    pause
    exit /b 1
)
echo    [OK]

echo [2/6] 建目录
if not exist "data\lessons" mkdir "data\lessons"
if not exist "models\SenseVoiceSmall" mkdir "models\SenseVoiceSmall"
echo    [OK] data\lessons  models\SenseVoiceSmall

echo [3/6] 生成配置文件
if exist "data\.config.yaml" (
    echo    [OK] data\.config.yaml 已存在，跳过
) else (
    copy ".config.yaml.example" "data\.config.yaml" >nul
    echo    [!] 已生成 data\.config.yaml —— 请打开它，把 api_key 换成你的 DeepSeek Key
    echo        https://platform.deepseek.com/api_keys
    start notepad "data\.config.yaml"
)

echo [4/6] 下载语音识别模型（约 900MB，已有则跳过）
if exist "models\SenseVoiceSmall\model.pt" (
    echo    [OK] model.pt 已存在
) else (
    echo    正在下载，请耐心等（可关闭窗口重跑，会续传逻辑不保证，但会跳过已完成）
    curl -L --retry 3 -o "models\SenseVoiceSmall\model.pt" "https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt"
    for %%A in ("models\SenseVoiceSmall\model.pt") do set SIZE=%%~zA
    if !SIZE! LSS 100000000 (
        echo    [X] 下载不完整（!SIZE! 字节），请重跑本脚本
        del "models\SenseVoiceSmall\model.pt" >nul 2>&1
        pause
        exit /b 1
    )
    echo    [OK]
)

echo [5/6] 生成示例音频（可选，用 edge-tts）
python "make_sample_lessons.py" --out "data\lessons" 2>nul
if errorlevel 1 (
    echo    [!] 跳过（没装 edge-tts 或没装 python）。可以稍后手动跑：
    echo        pip install edge-tts ^&^& python make_sample_lessons.py --out data\lessons
) else (
    echo    [OK] data\lessons 里已生成 2 课示例
)

echo [6/6] 启动服务端
docker compose up -d
if errorlevel 1 (
    echo    [X] 启动失败，先看 docker compose 的报错
    pause
    exit /b 1
)

echo.
echo 等待 10 秒让服务端完成初始化...
timeout /t 10 /nobreak >nul

echo.
echo ============================================
echo   完成。下面是你要填到 VoCat 里的地址：
echo ============================================
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /c:"IPv4"') do (
    set IP=%%i
    set IP=!IP: =!
    echo   OTA 地址:  http://!IP!:8003/xiaozhi/ota/
)
echo.
echo   （在 VoCat 配网页面「高级」里填这个地址）
echo.
echo   看日志： docker logs -f xiaozhi-esp32-server
echo   验证：   对设备说「播放 001」
echo.
pause

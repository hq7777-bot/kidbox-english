# 服务端一键包

把这个文件夹整个拷到旧笔记本上（比如 `D:\xiaozhi-server`），双击 `setup.bat`。

## 里面有什么

| 文件 | 用途 |
|---|---|
| `setup.bat` | 一键：检查 Docker、建目录、生成配置、下模型、起容器、打印 OTA 地址 |
| `docker-compose.yml` | 官方 compose（镜像走南大源，国内能拉） |
| `.config.yaml.example` | 配置模板（已改好 DeepSeek + 本地 ASR + 教材目录） |
| `make_sample_lessons.py` | 生成 2 课示例音频（用 edge-tts，不需要 key） |

## 三步

```bat
:: 1. 双击（或命令行）
setup.bat

:: 2. 它会把 data\.config.yaml 用记事本打开，填 DeepSeek Key
::    api_key: sk-你的key

:: 3. 再双击一次 setup.bat（这次会真正启动）
```

最后一次运行完，窗口里会打印：

```
OTA 地址:  http://192.168.1.25:8003/xiaozhi/ota/
```

把它填到 VoCat 配网页面的「高级」里。

## 验收

```bat
docker logs -f xiaozhi-esp32-server
```

看到 `OTA接口是 http://...:8003/xiaozhi/ota/` 就是成功。
然后对设备说「播放 001」，应该听到 "Hello! How are you?"

## 目录结构（脚本会自动建）

```
deploy/  (= 旧笔记本上的 D:\xiaozhi-server)
├─ docker-compose.yml
├─ setup.bat
├─ data/
│   ├─ .config.yaml        ← 你的 key 在这里（不进 git）
│   └─ lessons/            ← 教材音频
│       └─ L1/001_hello/001.mp3 …
└─ models/
    └─ SenseVoiceSmall/
        └─ model.pt        ← 900MB
```

## 常见问题

| 现象 | 处理 |
|---|---|
| `docker` 不是命令 | 装 Docker Desktop，装时勾 WSL 2，装完重启 |
| 模型下载慢/中断 | 重跑 `setup.bat`，它会跳过已有文件（文件不完整会自动删掉重下） |
| 容器起来就退出 | `docker logs xiaozhi-esp32-server` 看报错，多半是 model.pt 不完整 |
| 设备连不上 | 防火墙放行 8000/8003：`netsh advfirewall firewall add rule name="xiaozhi" dir=in action=allow protocol=TCP localport=8000,8003` |

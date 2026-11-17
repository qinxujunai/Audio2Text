# macOS 自用测试版

2026-11-17：Apple Silicon 本机开发验证，功能与发布状态以实际测试为准。此版本尚未进行 Developer ID 签名、公证、干净 macOS 安装验证或 Windows 回归验证，不作为公开正式版。

运行需要 macOS Apple Silicon，本机 `/opt/homebrew/bin/ffmpeg`；浏览器提取可使用 Chrome 或工作区内的 Playwright Chromium。首次使用需准备 SenseVoice 模型。应用数据在 `~/Library/Application Support/online.praxisai.wanxiang/workspace`；模型、浏览器和记录不包含在 DMG 中。

本机运行 Python 3.12，`uv venv --python 3.12 .venv`，安装 `requirements.txt` 及 `pyinstaller==6.22.3`，在 `web` 目录执行 `npm ci`；安装 Rust、Xcode 命令行工具后运行 `scripts/build_macos.sh`。代码签名和公证需另行配置。

本机开发版实际处理：本地 7.12 秒音频、公众号文章、抖音视频、B站视频各一条；独立应用和用户应用目录安装版各完成本地音频转写。平台限制、账号会话、网络及不同素材会影响结果；其他机器和平台不能据此视为通过。密钥使用 macOS 钥匙串，密钥访问可能需要系统授权。

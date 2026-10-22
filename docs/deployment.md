# Praxis AI｜无界笃行 / 万象成文 部署说明

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 项目维护文档 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

## 部署定位

这是一个单机、单实例、文件存储、单页交付型产品。

部署目标固定为：

- 保持项目级环境隔离
- 保持结果可交付
- 保持失败可解释
- 不依赖宿主机全局脏环境

## 运行时目录

推荐目录：

```text
workspace/
  runtime/
    models/
      faster-whisper/
        medium/
    playwright-browsers/
    ffmpeg/
    browser-profile/
  captures/
  artifacts/
  temp/
  logs/
```

说明：

- `models`、`playwright-browsers`、`ffmpeg`、`browser-profile` 都属于项目级运行时目录。
- `captures`、`artifacts`、`temp`、`logs` 都属于项目级数据目录。
- 视频预览件 `preview_media`、下载原件 `source_media`、音频导出件 `source_audio`、图片、Live 片段、`.txt/.md` 都会落到 `workspace/artifacts/<capture_id>/`。

## 启动前 preflight

应用启动前会检查以下项目级依赖。

### fatal

- `local_faster_whisper` 模式下模型目录不存在
- `openai_compatible` 配置不完整

### warning

- 项目级 Playwright Chromium 未安装
- 项目级 `ffmpeg / ffprobe` 未就绪
- 项目级浏览器会话目录为空
- 当前仍在使用 legacy 模型目录

## 推荐配置

### Windows 当前候选路线

2.3 候选的基础本地转写使用 SenseVoice int8；本机 CUDA 为 SenseVoice Small FP32 与独立 sherpa-onnx GPU SDK。CPU 与 GPU 的权重不同，不用它们的耗时比宣称纯 CUDA 加速。GPU 准备与安装验证见 [实施记录](release-2.3.0.md)；不要覆盖 CPU venv，不修改驱动或全局环境。下述 Faster-Whisper 与 OpenAI-compatible 是可选部署示例，不是当前本机默认模型。

### 可选本地 Faster-Whisper

```json
{
  "workspace_dir": "workspace",
  "model_path": "workspace/runtime/models/faster-whisper/medium",
  "ffmpeg_path": "workspace/runtime/ffmpeg/ffmpeg.exe",
  "transcription_provider": "local_faster_whisper"
}
```

### OpenAI-compatible

```json
{
  "workspace_dir": "workspace",
  "ffmpeg_path": "workspace/runtime/ffmpeg/ffmpeg.exe",
  "transcription_provider": "openai_compatible",
  "openai_compatible_base_url": "https://your-provider.example.com/v1",
  "openai_compatible_api_key": "your-api-key",
  "openai_compatible_model": "gpt-4o-mini-transcribe"
}
```

## 平台运行时提示

### 哔哩哔哩

- 主走 `yt-dlp`
- 命中字幕时跳过转写

### 小宇宙

- 页面解析只用于拿标题、封面、音频地址
- 正式交付只认 transcript
- `shownotes / description` 不再直接进入正式正文

### 抖音 / 小红书

- 属于高波动平台
- 优先公开链路，失败后再尝试项目级浏览器会话
- 推荐保留 `workspace/runtime/browser-profile`

如需刷新项目级会话：

```powershell
.\.venv\Scripts\python.exe -m scripts.refresh_browser_session
```

## 启动方式

推荐直接使用：

```powershell
.\.venv\Scripts\python.exe -m scripts.start_api
```

启动入口会在 preflight 和 uvicorn 之前检查目标端口，并校验 `frontend/dist/build-manifest.json` 的源码指纹。默认端口 `8000` 被旧服务占用时，脚本会直接失败并输出占用 PID / 命令提示，避免前端误连到旧 API；本地源码与 dist 不一致时会先自动执行一次前端构建，Node/npm 不可用或构建失败则拒绝启动，避免把旧 UI 提供给用户。Docker 的 `cloud_preview` 镜像在构建阶段已经生成前端，运行时没有 Node 时信任同镜像内的构建清单。临时端口：

```powershell
$env:AUDIO2TEXT_API_PORT = "8001"
.\.venv\Scripts\python.exe -m scripts.start_api
```

如需在本机按发布门槛完整复验一轮：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate
```

只跑后端、前端和 UI smoke，不跑 Docker：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate --skip-docker
```

Docker 构建默认最多等 300 秒；超过后应停止并定位卡在 `npm ci`、`pip install`、`playwright install` 还是系统 Docker 层，不继续盲等：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate --docker-build-timeout 300
```

Windows 桌面包必须通过带新鲜度校验的脚本构建，不能直接把 `web/src-tauri/target` 中的旧 exe 或旧安装包当成当前版本：

```powershell
.\scripts\build_desktop.ps1
```

该脚本会先比较 `frontend/dist/build-manifest.json` 与当前前端源码指纹；不一致就重新构建前端，然后构建并冒烟验证 sidecar，最后执行 Tauri 打包，并确认 exe 与 NSIS 安装包确实在本次构建后生成。`-Debug` 可用于本地调试包，`-SkipSidecar` 只适用于已经单独完成 sidecar 验证的场景。脚本需要本机已有 Node/npm、Rust `cargo`/`rustc`，不会自动安装系统工具链。

最终上线前必须补跑：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate `
  --final `
  --with-live-smoke `
  --live-smoke-base-url "http://127.0.0.1:8000" `
  --deployment-contract-base-url "https://wanxiang.praxisai.online" `
  --extraction-benchmark-report "workspace\\benchmarks\\extraction-release.json" `
  --transcription-benchmark-report "workspace\\benchmarks\\transcription-release.json"
```

`--final` 还会拒绝缺少 60 链接 / 72 音频片段三轮 benchmark 证据的发布；部署契约会检查 HTML 与深链接 `no-store`、hash 资源 `immutable`，以及前后端构建 ID 是否可读。

## Docker

构建：

```powershell
docker build -t praxis-audio2text .
```

### 方式一：挂载本地 Faster-Whisper 模型

```powershell
docker run --rm --name praxis-audio2text -p 8000:8000 `
  -v "${PWD}\workspace:/app/workspace" `
  praxis-audio2text
```

要求：

- `workspace/runtime/models/faster-whisper/medium` 已经存在可用模型文件。
- 如需浏览器提取链路，挂载后的 `workspace/runtime/playwright-browsers` 也要可用。

### 方式二：使用 OpenAI-compatible 转写

不挂载本地模型目录时，应通过环境变量切换转写 provider：

```powershell
docker run --rm --name praxis-audio2text -p 8000:8000 `
  -v "${PWD}\workspace:/app/workspace" `
  -e AUDIO2TEXT_TRANSCRIPTION_PROVIDER=openai_compatible `
  -e AUDIO2TEXT_OPENAI_COMPATIBLE_BASE_URL="https://your-provider.example.com/v1" `
  -e AUDIO2TEXT_OPENAI_COMPATIBLE_API_KEY="your-api-key" `
  -e AUDIO2TEXT_OPENAI_COMPATIBLE_MODEL="gpt-4o-mini-transcribe" `
  praxis-audio2text
```

当前约定：

- 镜像内会自行执行前端构建。
- Docker 不依赖宿主机已有 `frontend/dist`。
- 容器内默认使用镜像自带 `/usr/bin/ffmpeg`。
- Docker context 应排除 `.venv`、`web/node_modules`、`frontend/dist`、`tests_runtime`、`workspace`。
- 镜像默认按 `cloud_preview` 预览模式启动；未配置模型或 OpenAI-compatible provider 时，首页仍可访问，音视频转写会给出明确的恢复提示。
- Faster-Whisper 模型默认不内置在镜像中；要让本地转写可用，必须挂载模型目录。
- `workspace` 挂载会覆盖容器内 `/app/workspace`，因此模型、Playwright Chromium、浏览器会话和 capture/artifact 数据都以宿主机挂载目录为准。

## 免费公网预览

当前目标是先拿到一个能访问的链接，不做账号、支付、积分、小程序或 App。

### 方式一：本机临时公网演示

适合当天演示。双击或在 PowerShell 执行：

```powershell
.\start_api.bat
```

默认会做四件事：

- 检查本地服务是否可用；不可用时自动启动 `scripts.start_api`。
- 优先创建 Cloudflare Quick Tunnel。
- 如果 Cloudflare 建连失败，自动回退 localtunnel。
- 把最终链接复制到剪贴板，并写入 `tests_runtime/public_preview/current_url.txt`。

只做本地开发或排障时：

```powershell
$env:AUDIO2TEXT_LOCAL_ONLY = "1"
.\start_api.bat
```

注意：

- 电脑不能关，启动窗口不能关。
- localtunnel 首次访问可能出现 IP 确认页；按启动器提示输入页面显示的 IP 即可继续。
- 这仍是临时演示链接，不是正式云部署。

### 内部 tunnel helper

日常启动只使用 `start_api.bat`。它会先调用 `scripts/start_api_bootstrap.ps1` 做环境自检和自愈，再调用 `scripts.public_preview`；公网链路优先尝试 Cloudflare Quick Tunnel，失败时回退 localtunnel。

启动自检负责：

- `.venv` 缺失、损坏或 uv 托管 Python 路径漂移时，自动用 Python 3.11 重建虚拟环境。
- 按 `requirements.txt` 的锁定版本安装依赖，减少换电脑或未来依赖漂移造成的故障。
- 检查前端构建产物、模型、ffmpeg、Playwright Chromium、CUDA runtime 和端口。
- 来源站点的瞬时网络或浏览器波动最多自动重试两次；失效链接、平台验证和需要用户操作的状态不会盲目重试。可通过 `AUDIO2TEXT_AUTOMATIC_RETRY_LIMIT` 下调或关闭。

`scripts/start_cloudflare_tunnel.py` 和 `scripts/start_localtunnel.py` 只保留为内部 helper，供公网预览编排器自动调用，或排障时临时使用。不要把它们作为用户文档里的同级启动入口。

排障时如果确实需要单独检查 tunnel helper，先启动 `scripts.start_api`，再临时运行对应 helper；排障结论回到 `start_api.bat` 这条用户入口上验证。

### 方式二：Hugging Face Spaces Docker

适合做免费云端预览。它能给 `https://<namespace>-<space>.hf.space` 链接，但免费磁盘不是长期持久存储，运行产物只适合演示。

前提：

- 已安装项目 `.venv` 内的 `hf` CLI；当前仓库可直接使用 `.\.venv\Scripts\hf.exe`。
- 已执行 `.\.venv\Scripts\hf.exe auth login`。
- 已准备 OpenAI-compatible 转写服务；不要在免费 Space 里默认依赖本地 Faster-Whisper 模型目录。

上传：

```powershell
.\.venv\Scripts\python.exe -m scripts.deploy_huggingface_space
```

如果想先创建私有 Space：

```powershell
.\.venv\Scripts\python.exe -m scripts.deploy_huggingface_space --private
```

默认 Space 名称是 `wanxiang-chengwen-preview`。脚本会读取当前 HF 登录用户并部署到 `你的用户名/wanxiang-chengwen-preview`；如需自定义名称，可显式传入 `your-username/your-space-name`。

Space 变量 / Secret：

```text
AUDIO2TEXT_API_HOST=0.0.0.0
AUDIO2TEXT_API_PORT=8000
AUDIO2TEXT_DEPLOYMENT_MODE=cloud_preview
AUDIO2TEXT_PUBLIC_PREVIEW_MODE=1
AUDIO2TEXT_ALLOW_DEGRADED_START=1
AUDIO2TEXT_TRANSCRIPTION_PROVIDER=openai_compatible
AUDIO2TEXT_OPENAI_COMPATIBLE_BASE_URL=<your-provider-base-url>
AUDIO2TEXT_OPENAI_COMPATIBLE_API_KEY=<secret>
AUDIO2TEXT_OPENAI_COMPATIBLE_MODEL=<model>
```

脚本会自动写入前 6 个公开变量。OpenAI-compatible 的 base URL、API key、model 需要按实际 provider 配置；API key 必须放 Space Secret。未配置这三项时，云端首页仍可访问，图文和字幕类链接可先体验，本地音视频上传会给出“需要配置转写服务”的提示。

当前仓库 README 顶部已经带 Hugging Face Space metadata：

```yaml
sdk: docker
app_port: 8000
```

限制：

- 免费 Space 的 `workspace` 数据可能随重启、重建或休眠丢失，不要当正式资料库。
- OpenAI-compatible provider 的 API key 只能放在 Space Secret，不要写进仓库。
- 高波动平台仍可能因为浏览器会话、验证码、平台风控失败。
- 这条路径用于预览和演示，不等同正式生产上线。

## 部署后检查

- `/health` 返回 `status=ok`
- `/health` 中的 `commit`、`frontend_git_sha` 与 `frontend_build_id` 可用于确认后端源码、前端源码和静态产物属于同一发布版本；本地启动未显式提供 `AUDIO2TEXT_COMMIT` 时会自动读取当前 Git 短 SHA。
- `/config` 返回产品公开配置，不暴露内部绝对路径
- 首页可访问，能正常进入处理中和结果页
- 高波动平台在无浏览器会话时给出 warning，而不是静默卡死

## 仓库边界

- `frontend/dist/` 属于构建产物，应由构建生成
- `tests_runtime/` 与 `workspace/temp/` 可按需清理
- 推荐统一执行 `.\.venv\Scripts\python.exe -m scripts.cleanup_runtime --yes`，避免手工删漏目录
- `Assets/Models/...` 属于 legacy fallback，未迁移前不应直接删除

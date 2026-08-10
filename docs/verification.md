# Praxis AI｜无界笃行 / 万象成文 验收清单

## 自动化检查

后端：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

前端类型检查：

```powershell
cd web
npm exec tsc -- --noEmit
```

前端构建：

```powershell
cd web
npm run build
```

本地发布门槛单入口：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate
```

非 Docker 门槛：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate --skip-docker
```

Docker 构建限时门槛：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate --docker-build-timeout 300
```

最终上线前必须执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.release_gate `
  --final `
  --with-live-smoke `
  --live-smoke-base-url "http://127.0.0.1:8000"
```

## 本地 API 启动

```powershell
.\start_api.bat
```

检查：

- 首次启动或 `.venv` 损坏时，入口应自动重建 Python 3.11 虚拟环境并安装 `requirements.txt` 的锁定依赖
- `frontend/dist` 缺失时，入口应在 `npm` 可用时自动构建前端产物；无法构建时必须给出中文错误
- 启动前自检应覆盖 Python、依赖、模型、ffmpeg、Playwright Chromium、CUDA runtime 和端口
- 启动前如果 `8000` 已被旧服务占用，脚本应直接失败并提示占用 PID / 命令，不应继续进入 uvicorn
- `http://127.0.0.1:8000/health` 返回 `status=ok`
- `http://127.0.0.1:8000/config` 不暴露运行时绝对路径
- 首页、处理中、结果页都可正常打开

## 项目级运行时检查

应确认这些目录都在项目内：

```text
.venv
workspace/runtime/models/faster-whisper/medium
workspace/runtime/playwright-browsers
workspace/runtime/ffmpeg/ffmpeg.exe
workspace/runtime/browser-profile
```

补充检查：

- `workspace/runtime/browser-profile` 为空时，系统仍可启动，但高波动平台会给出会话 warning
- 日常启动不应要求用户手动修 `.venv`；若 uv 托管 Python 路径变化，`start_api.bat` 应自动重建环境
- 如需刷新项目级浏览器会话，应运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.refresh_browser_session
```

## 平台契约检查

自动化测试重点覆盖：

- URL 路由识别
- adapter 选择
- provider 顺序
- fallback 元信息
- `extractor_used`
- `fallback_used`
- `transcript_status`
- `text_source`
- `subtitle_source`
- `selected_language`
- `retryable`
- `progress_percent`

重点文件：

- [tests/test_platform_contracts.py](../tests/test_platform_contracts.py)
- [tests/test_capture_api.py](../tests/test_capture_api.py)
- [tests/test_pipeline_and_store.py](../tests/test_pipeline_and_store.py)

## 交付行为验收

### 标题 / 正文

- 结果页 Hero 只显示标题，不再吞正文长段落
- 正文区只显示正式交付正文
- 桌面端正文卡片在卡片内部滚动，浏览器整页不因正文长度无限变长
- 正文滚动过程中外层圆角始终完整，不出现“滚动后变成直角”
- `复制全文`：
  - 有真实 Markdown 时复制 Markdown
  - 没有时复制纯正文
- `.txt` 始终可下载
- `.md` 只在真实 Markdown 可用时显示

### 视频

- 有 `preview_media` 时，页面优先播放 `preview_media`
- 下载原视频使用 `source_media`；有音轨时显示 `source_audio` 音频下载
- 桌面端视频卡片与正文卡片等高
- 页面不额外叠自定义悬浮全屏 / 下载按钮
- 下载音频 / 下载视频按钮 hover / focus 时边框清晰可见
- 没有视频 artifact 但文本成功时，页面要明确提示“文本已完成，原视频暂未成功下载”

### 图文与 Live 图

- 微信公众号与小红书图文只保留正文相关图片
- 微信公众号命中验证页时应自动回退浏览器会话，不得把验证页文本当正文交付
- 微信公众号图片文章模板应带出正文图片区，不得只剩文字摘要
- 微信公众号图文结果应按图片内容去重，不得把封面图与正文首图重复交付
- 图片较多的图文任务在“整理结果”阶段应继续推进真实进度，不得长期卡在 96% 一类固定百分比
- 图文结果应在本地镜像与 ZIP 未完成时也能直接进入 `done`，并优先用源图展示
- 图片卡不应堆大块 footer 和多余主按钮，只保留轻量 hover 查看图标、`LIVE` 角标与当前下载动作
- 图片卡默认不展示 `KB` 体积、底部 `Live` 主按钮或居中的“查看图片”文字按钮
- 点击图片后，应打开全屏级模糊背景查看层；点击图片外区域可关闭，查看层内不应再出现“放大后还要继续往下滚”的二次滚动
- 查看层页码应进入底部工具条，鼠标滚轮用于切换上一张 / 下一张；静态图通过底部工具条显式缩放、旋转，放大后可拖拽平移
- 左右切换按钮与底部工具条应在鼠标悬停媒体时轻量显现，不抢观感，关闭后返回图片列表
- `images.zip` 未就绪时，“全部下载”应显示准备中或禁用，不得拖住整条 capture 停在处理中
- 小红书 Live 图拿到动态资源时交付“静态图 + Live 片段”
- Live 图桌面端悬停卡片即可自动播放，移出停止；进入查看层后默认直接进入动态态，按真实视频比例显示，仍可切换“图片 / Live”

### 小宇宙

- 小宇宙正式交付只认 transcript
- `shownotes / description / podcast.description / og:description` 不得进入 `primary_text`
- 旧的 `page_notes / shownotes / description / jsonld_description` 缓存结果不得继续复用

### 首页最近记录

- 默认显示 4 条
- 展开后最多显示 12 条
- 卡片等高稳定，不因标题长短出现高低不齐
- 单条删除支持撤销
- 清空全部支持撤销
- 失败记录默认不进入主列表

## 人工页面审计

至少完整走一轮以下界面：

- 首页输入区
- 处理中页面
- 文本结果页
- 视频结果页
- 图文 / Live 图结果页
- 最近记录展开、单条删除、清空与撤销

重点人工检查：

- 白底按钮 hover / focus 时边框是否依然清晰
- 桌面端结果页是否保持“上方双栏 + 下方 meta footer”
- meta footer 是否左对齐稳定，不与主卡片冲突
- 桌面和手机宽度下操作区不会随机塌成难看的纵向布局

当前最小 UI smoke 已自动覆盖：

- 首页 Hero 输入框与最近记录渲染
- 最近记录展开、删除、撤销链路
- 提交链接后首次读取若遇到瞬时 503，页面仍会进入处理中而不是误判失败
- 视频结果页正文内部滚动、meta footer 左对齐、按钮边框 hover 稳定
- 图文 / Live 图结果页操作按钮渲染

## Docker 验收

构建：

```powershell
docker build --progress=plain -t praxis-audio2text .
```

本地发布门槛里的 Docker build 默认 300 秒超时。超过后先看最后输出的构建步骤，不要继续盲等。

运行方式一：挂载本地 Faster-Whisper 模型。

```powershell
docker run --rm --name praxis-audio2text -p 8000:8000 `
  -v "${PWD}\workspace:/app/workspace" `
  praxis-audio2text
```

运行方式二：使用 OpenAI-compatible 转写。

```powershell
docker run --rm --name praxis-audio2text -p 8000:8000 `
  -v "${PWD}\workspace:/app/workspace" `
  -e AUDIO2TEXT_TRANSCRIPTION_PROVIDER=openai_compatible `
  -e AUDIO2TEXT_OPENAI_COMPATIBLE_BASE_URL="https://your-provider.example.com/v1" `
  -e AUDIO2TEXT_OPENAI_COMPATIBLE_API_KEY="your-api-key" `
  -e AUDIO2TEXT_OPENAI_COMPATIBLE_MODEL="gpt-4o-mini-transcribe" `
  praxis-audio2text
```

检查：

- Docker 构建不依赖宿主机已有 `frontend/dist`
- 镜像启动后首页可访问
- 未挂载本地模型目录且未配置 OpenAI-compatible provider 时，预览模式仍可启动首页，但音视频转写应给出明确失败提示
- 切换到 `openai_compatible` 并配置完整变量时，音视频转写不再依赖本地模型目录
- `workspace` 挂载后，Playwright Chromium、模型和浏览器会话都来自宿主机挂载目录
- 验收前可先执行 `.\.venv\Scripts\python.exe -m scripts.cleanup_runtime --yes`，清掉旧截图、旧 probe 和历史 smoke 结果，避免把临时产物误当成正式交付内容

## 免费公网预览验收

公网预览只验收一条用户入口：

```powershell
.\start_api.bat
```

检查：

- 输出中出现最终公网链接。
- 链接已写入 `tests_runtime/public_preview/current_url.txt`。
- 关闭启动窗口后链接失效；启动窗口保持打开时，公网首页可访问。
- 如果回退到 localtunnel，首次访问的 IP 确认页应符合窗口提示，不视为产品报错。
- `/health` 返回 `status=ok`
- 提交无效输入时能进入中文失败态
- 已完成 capture 的结果页刷新后仍可打开
- artifact 下载链接能返回文件或明确失败
- `scripts/start_cloudflare_tunnel.py` 和 `scripts/start_localtunnel.py` 只在排障时单独运行；它们不是发布验收里的用户入口。

本地-only 排障入口：

```powershell
$env:AUDIO2TEXT_LOCAL_ONLY = "1"
.\start_api.bat
```

### Hugging Face Spaces

上传：

```powershell
.\.venv\Scripts\python.exe -m scripts.deploy_huggingface_space your-username/praxis-audio2text
```

如果尚未登录：

```powershell
.\.venv\Scripts\hf.exe auth login
```

检查：

- Space build logs 没有 fatal error
- Space 配置了 `AUDIO2TEXT_TRANSCRIPTION_PROVIDER=openai_compatible`
- API key 只存在 Space Secret，不在仓库文件中
- Space 仓库和镜像中不包含本地 Faster-Whisper 模型目录
- `https://<namespace>-<space>.hf.space/health` 返回 `status=ok`
- 首页、失败态、轻量图文链接、短音视频样例至少各验收一次

## Live Smoke

默认样例文件：

- [tests/live_smoke_samples.json](../tests/live_smoke_samples.json)

运行：

```powershell
$env:AUDIO2TEXT_LIVE_SMOKE_BASE_URL = "http://127.0.0.1:8000"
.\.venv\Scripts\python.exe -m unittest tests.test_platform_live_smoke
```

如需本地私有样例覆盖：

```powershell
$env:AUDIO2TEXT_LIVE_SMOKE_BASE_URL = "http://127.0.0.1:8000"
$env:AUDIO2TEXT_LIVE_SMOKE_SAMPLES = "tests/live_smoke_samples.local.json"
.\.venv\Scripts\python.exe -m unittest tests.test_platform_live_smoke
```

## Capture Diagnostics Checks

- Slow tasks should be inspected with `.\.venv\Scripts\python.exe -m scripts.diagnose_capture <capture_id>`.
- Each processed capture should have a per-capture trace at `workspace/logs/capture_traces/<capture_id>.jsonl`.
- `capture_pipeline.log` should include a final stage-duration summary so recovery time, extraction time, transcription time, and artifact time can be separated.
- Logs under `workspace/logs` are rotated and remain inside the project workspace.

## Long Title Layout Checks

- Desktop hero titles are clamped to two lines; mobile hero titles are clamped to three lines.
- Long titles keep the full text in the element `title` attribute for inspection.
- Text and video result panels should remain visually equal-height on desktop, with long body text scrolling inside `.result-content-shell`.

## Result Source Link Checks

- When `canonical_url` is present, the result page should render a `查看来源` action chip in the bottom meta footer.
- Clicking `查看来源` should open the source in a new tab and keep the current result page intact.
- Long titles must truncate before they squeeze or hide the `查看来源` chip on desktop, tablet, or mobile widths; title chips should use content width first, and meta/action chips should follow without a large forced middle gap.

# Praxis AI｜无界笃行 / 万象成文 发布收口说明

## 当前发布口径

当前版本按“单页交付型产品”发布，不按工作台或多角色系统发布。

对外只承诺三类能力：

- 音频 / 视频转文字
- 图文正文与正文图片提取
- 视频结果页预览并下载原视频、原音频

Windows 安装包是完整主产品；云端用于限额演示。目标电脑不需要
Python、uv、CUDA 或单独安装 Chromium。首次使用安装 FFmpeg 9.0
Essentials 与 SenseVoice int8 两个校验运行包，总下载约 222 MB。

## 当前真实交付形态

### 首页

- 单页 Hero 输入，不跳转旧工作台
- 最近记录默认展示 4 条，展开后最多 12 条
- 支持单条删除撤销、清空全部撤销

### 处理中

- 显示真实阶段百分比与当前动作说明
- 不使用前端本地伪进度

### 结果页

- Hero 只显示标题与摘要说明
- 桌面端采用“上方双栏主内容 + 下方 meta footer”
- 正文卡片内部滚动，视频卡片与正文卡片等高
- meta footer 左对齐展示平台、内容类型、时长与查看来源
- `.txt` 始终保留，`.md` 只在真实 Markdown 可用时出现
- 视频预览优先 `preview_media`，下载原视频使用 `source_media`，有音轨时提供 `source_audio`

## 当前不应误宣称的内容

- 不应宣称“任意公开链接都稳定成功”
- 不应宣称“高波动平台不受验证码 / 会话影响”
- 不应宣称“小宇宙支持直接抓口播正文”
- 不应宣称“下载视频与预览视频完全相同”

## 发布前硬门槛

- 后端自动化测试通过
- Worker 安全测试与 npm 官方安全审计通过
- 前端类型检查通过
- 前端生产构建通过
- Rust `fmt` / `clippy -D warnings` 通过
- NSIS 在干净目录静默安装成功，安装后 WebView 能调用 Tauri 命令并访问 `/config`
- 强制结束桌面主进程后，sidecar 必须在 3 秒内退出
- 运行包清单、下载大小和 SHA-256 与 GitHub Release 资产一致
- `start_api.bat` 可在 `.venv` 缺失、损坏或 Python 路径漂移时自动重建项目虚拟环境
- Python 依赖必须按 `requirements.txt` 的锁定版本安装，避免换电脑或隔一段时间后依赖漂移
- 默认端口未被旧服务占用；若被占用，`scripts.start_api` 必须清晰失败并提示占用进程
- 用户可见启动入口只保留一条：`start_api.bat`；默认启动本地服务和公网预览，`AUDIO2TEXT_LOCAL_ONLY=1` 仅用于本地排障
- Docker 镜像可从当前仓库直接构建，但不作为 Windows 主产品的前置条件
- Docker 运行口径必须明确选择“挂载本地 Faster-Whisper 模型”或“使用 openai_compatible 转写”
- Hugging Face Spaces 预览必须使用 `openai_compatible`；不要把本地 Faster-Whisper 模型打进镜像或上传到 Space 仓库
- README / 部署 / 验收 / 发布文档与真实代码行为一致
- `.\.venv\Scripts\python.exe -m scripts.release_gate` 可在本机完整通过
- Docker 构建必须带可控超时；如果超过 300 秒仍无结果，先定位构建卡点，不继续盲等
- Hugging Face 上传前必须完成 `.\.venv\Scripts\hf.exe auth login`
- HF Space 默认部署命令是 `.\.venv\Scripts\python.exe -m scripts.deploy_huggingface_space`；脚本会使用当前登录用户和默认 Space 名 `wanxiang-chengwen-preview`
- 云端未配置 OpenAI-compatible 转写服务时，首页仍应可用，本地音视频文件上传前必须给出明确提示
- 最终发布前必须执行 `.\.venv\Scripts\python.exe -m scripts.release_gate --final --with-live-smoke --live-smoke-base-url "http://127.0.0.1:8000"`

本轮 2026-08-11 证据：哔哩哔哩、小宇宙、抖音、微信图文通过；
小红书图文与 Live（15 图、14 个 Live 片段）通过。小红书视频样本触发
平台会话验证，YouTube 在当前中国网络约 53 秒内明确失败，不得写成已通过。

## 建议发布前复验的平台样例

- YouTube：1 条字幕视频
- 哔哩哔哩：1 条字幕或简介明显的视频
- 小宇宙：1 条口播 transcript 样例
- 抖音：1 条公开视频样例
- 小红书：1 条图文、1 条视频、1 条 Live 图样例
- 微信公众号：1 条图文样例

## 发布后仍应关注的边界

- 抖音、小红书仍属于高波动平台，成功率依赖公开链路与项目级浏览器会话
- Windows CPU 默认使用 SenseVoice int8；CUDA 机器可继续使用项目级 Faster-Whisper medium 精准档
- 小红书视频发布样本需要新鲜分享链接与有效项目级浏览器会话
- `Assets/Models/...` 当前仍是 legacy fallback，不应在未迁移前直接删除

## 仓库清理口径

- `tests_runtime/` 属于测试临时产物，可清理
- `workspace/temp/` 属于运行期临时目录，可清理
- 推荐统一执行 `.\.venv\Scripts\python.exe -m scripts.cleanup_runtime --yes`
- `frontend/dist/` 属于构建产物，应由构建生成
- `workspace/` 属于运行期数据目录，不应混入源码提交逻辑

# AGENTS.md

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-23** · 项目维护文档 · [最新状态](docs/project-status.md) · [文档索引](docs/README.md)
> v2.3.0 已于 2026-10-23 正式发布；历史验证仍以各自测试日期和安装包摘要为准。
<!-- project-doc-status:end -->

本文件写给后续 AI 编程助手 / Codex。它不是产品宣传稿，只记录当前仓库已经落地、需要遵守的工作规则。

接手前按顺序读：本文件 →「不许回退的决定」→ [`docs/project-status.md`](docs/project-status.md)（源码、安装版、Release、官网各自的状态）。

## 当前事实（2026-10-23）

- **分支**：`clean-main`（默认）已合入通过验收的 2.3；正式版 v2.3.0 冻结来源 `a192250`，安装包 SHA-256 `a4a7a62ccf597210587bf2a4f837aa78c7ad62d2c4cf9813634c91a6b3d2b1a3`。`codex/wanxiang-platform-audio-ui` 保留开发历史；之后的文档/合并提交不冒称安装来源。
- **结构**：
  - `app/main.py` FastAPI 入口；`app/pipeline.py` 一条 Capture 的流程（识别来源 → 提取 → 转写 → 组装 → 交付文件，后台补齐图片和原视频）。
  - `app/source_adapters.py` 每个平台的取数链（直连 → 开源工具 → 浏览器会话）；`app/extractors.py` 调度与下载；`app/browser_provider.py` Playwright 兜底。
  - `app/platforms/`：按平台拆出的纯函数（可用录制样本测试）。目前有 `douyin.py`（分享页接口）、`ranged.py`（多连接分段下载）。**新的平台逻辑放这里**，其余平台以后按同样方式从 `extractors.py` 迁出，一次一个，迁移时把测试的 `patch.object(extractors, …)` 一并改到新模块。
  - `app/providers/transcription.py` 转写（SenseVoice，CPU / CUDA 进程隔离）；`web/` React 前端 + Tauri 桌面壳；`site/` 官网（Pages 手动触发部署）。
- **发布**：CI 只出候选安装包；正式 Release 要用他电脑上的三轮基准、安装验收和同文件上传（见下文协议）。云端机器的 IP 会被 B 站 412 拦截、抖音分享页不返回内容，真实链路验证要在他的电脑上做。

## 不许回退的决定

每条都有原因；要改，先在这里写清新理由。

| 决定 | 原因 | 来源 |
| --- | --- | --- |
| B 站视频先下 DASH 音轨，立刻转写交付文字；原视频在后台重新取地址下载并合并（`deferred_video`，`deferred_video.json` 让重启后继续） | 三轮基准里的 4 次超时都是转写前要先下完整段原视频（样本 260 MB）。补齐失败只影响原视频，文字保留 | `6033f39` |
| 后台原视频用 `app/platforms/ranged.py` 多个 Range 连接下载，不支持时退回单连接 | B 站 CDN 按连接限速；与 Bili23-Downloader、aria2c `-x` 同一做法 | `0751581` |
| 后台原视频仍优先渐进式资源，不因改为音轨优先而固定选 DASH；渐进式不可用才回落 DASH | 本机发现同一样本 DASH 仅 854×462，渐进式原视频为 1280×692。恢复原有画质选择后，后台实测 66.42 秒完成，文件 SHA-256 与旧原视频一致；文字仍先交付，正文与文字附件哈希不变 | `f7b9b33`；`docs/platform-benchmark-20261022.md` |
| 抖音先走分享页接口 `iesdouyin.com/web/api/v2/aweme/slidesinfo/`（视频、图文两种请求，各问两次），web detail 接口只作最后一试 | 免登录、免签名，能拿到标题、作者、时长、无水印播放地址（2026-10-22 实测 8.6 MB 视频 10.5 秒）。旧的 `aweme/iteminfo` 已停用（返回空），detail 接口没有 a_bogus 签名会被 403，这正是「要求有效会话」失败的来源。**`iteminfo` 不要加回来** | `0751581` |
| 小红书读页面 `__INITIAL_STATE__`，兼容桌面 `note.noteDetailMap` 与手机 `noteData` | 与最成熟的 XHS-Downloader 同一做法（它是 GPL-3.0，只能借鉴做法，不能并入代码）。没有 `xsec_token` 的旧链接读不到是平台限制 | `f063cec` |
| 发布门槛：60 条独立链接 × 三轮 × 零超时，失败和风控留在分母，五个平台每轮都要有完整样本 | 不放宽。基准行的 `video_deferred` 如实说明该行不计原视频下载时间 | `docs/platform-benchmark-20261021.md` |
| YouTube 不支持、不宣传（2026-10-13 用户决定） | 新输入在网络请求前拒绝，不回退通用网页 | 见协议一节 |
| 校对不覆盖原 Capture；建议默认不选中，逐处确认后存独立文件 | 模型不能保证语义正确 | 见协议一节 |
| 结果页宽度上限：外层 1100px、工作区 980px、高 820px 居中 | 2026-10-11 用户拒绝扩宽 | 见协议一节 |
| 品牌图标只用 `web/public/brand-mark.svg` 的圆点 | 用户指定 | 见协议一节 |
| 官网文案说用户能做什么：「贴一个链接，拿回全部文字」「下载的每个文件，都能核对」「下一条链接，交给它」；中文标题只在标点处换行（`word-break: keep-all`） | 2026-10-22 按 Apple 产品页标准改写 | `d9d2a67`、`6708808` |

## 已知问题与待办

- **2.3 已发版**。同一批 60 条三轮 141/180 完整、零超时，validator 通过；本机、干净 Windows 同文件验收和正式 Release 完成。39 次失败全部保留，不能宣传所有链接都成功；官网部署与下载摘要以 `docs/project-status.md` 的最终回执为准。
- 抖音：分享页接口偶尔返回空（已重试两次）；作者删除或私密的作品取不到。
- 小红书：没有 `xsec_token` 的旧链接读不到。
- 小宇宙读 `__NEXT_DATA__`，3/30 来源不可读取；公众号 30/30。
- 安装包未签名。已有 6098 段、6.082 小时、三轮人工参考证据通过本次 validator；本次未重跑准确率，也未更换模型。跨模型与 CPU/CUDA 速度仍须分别计量。
- `app/extractors.py`（约 2800 行）和 `browser_provider.py`（约 1900 行）仍偏大，按上文方式逐个平台迁出。

## 交接日志

- **2026-10-23，Codex**：冻结 `a192250`，统一构建及完整资源树校验通过；本机安装回滚位于 `workspace/temp/product-rollback-20261022-235642/`。原 10 条历史和加密设置哈希不变，新增一条 B 站验收记录。真实安装版 SenseVoice CUDA：25.452 秒出文字、92.970 秒原视频就绪，1280×692 播放通过；普通/最大化 1100/980/820、实际 144 DPI、DeepSeek 合成校对、原文保存、正常退出和关闭调试端口通过。同文件干净 Windows CI `39811228460` 验收通过，Quality Gate `39807590791` 通过；v2.3.0 已正式发布，SHA-256 见当前事实。默认分支合并 `6bd2265` 仅处理文档冲突，保留线上 README 创作缘起；应用代码与发布冻结提交一致。手动 Pages `39812322694` 成功，线上桌面/手机/语言/演示/品牌资源复核通过；官网下载同文件 SHA-256 一致。合并后另 20 项文档、官网及品牌回归通过。标签自动触发的重复重建 CI 已取消，不用其候选替换正式验收文件。
- **2026-10-22，Codex**：按用户附件执行线上同步，候选对齐 `42ba8a5`，默认分支对齐 `2ceaab7`；本地计划、品牌候选与工具项目文件保存在本地 `backup/local-20261022` / `82745d0`，未合回。固定 60 条三轮完成，141/180 完整、零超时，五个平台每轮完整成功，validator `passed: true`；39 次失败全部保留。补修后台原视频画质回退 `f7b9b33`，两个回归先红后绿；394 项后端（1 跳过）、32 项浏览器回归及 npm 正式依赖审计通过，独立审查无阻断。真实 B 站文字 117.823 秒；修复后的后台原视频单独补验 66.42 秒、1280×692，原正文哈希不变。旧失败抖音样本仍失败，分享 API 返回空详情及过滤码，不能声称登录即可恢复。证据见 `docs/platform-benchmark-20261022.md` 与本地 `tests_runtime/product-upgrade/benchmark-20261023/`。本条记录时新源码尚待冻结打包、实际安装和干净 Windows 同文件验收，不宣称已发布。
- **2026-10-22，Claude**：处理页加计时与预计剩余时间（`e728d4a`）；B 站先文字后视频（`6033f39`）；抖音改走分享页接口、B 站后台分段下载、新建 `app/platforms/`（`0751581`）；官网文案与换行（clean-main `d9d2a67`、`6708808`）；本文件重写，2026-05 维护日志移到 [`docs/history.md`](docs/history.md)。验证：Linux 全量测试与改动前同一失败集（6 项依赖 Windows/本机环境），Quality Gate 通过；真实 B 站链路未验证（云端 412）。
- **2026-10-21 及以前，Codex（Praxis）**：2.3 候选实现、三轮基准、安装验收、官网品牌维护，见 `docs/project-status.md` 与 `docs/release-2.3.0.md`。

## 工作方式

- 以 GitHub 上的代码与发行版为准（2026-10-22 用户：不用管本机还有没有没推的东西）。
- 每个子问题先查 GitHub 上成熟、仍在维护的开源做法，不重复造轮子；换了做法要在「不许回退的决定」登记原因与提交号。GPL/AGPL 代码只借鉴做法，不并入（本项目 Apache-2.0）。
- 修改前先读相关代码、配置、文档和测试，理解现状后再动手。
- 默认最小改动，优先复用已有结构、命名、类型、脚本和目录约定。
- 结构调整要有明确收益并一次一块（例如按平台迁出 `extractors.py`）；不擅自改 API contract、产品方向或视觉风格。
- 修 bug 先定位根因，避免到处堆 if 或补丁叠补丁。
- 前端、后端、API 类型、Docker、README、docs 和测试要同步；改了运行方式或接口就必须同步文档。
- 修改后必须说明改动内容、原因、验证命令、未覆盖风险。
- 文档只写已验证的稳定事实，不把猜测、试验性决策或未来愿景写成承诺。

## 项目稳定事实

- 产品名：`万象成文`；母品牌：`Praxis AI｜无界笃行`。
- 当前形态是单页交付型产品：链接 / 文件输入，同页处理，同页展示结果。
- 当前能力只收口到三类：
  - 音频 / 视频转文字
  - 图文链接提取正文与正文图片
  - 视频结果页预览并下载原视频
- 后端是 FastAPI，入口是 `app/main.py`，启动入口是 `python -m scripts.start_api` 或 `start_api.bat`。
- 前端源码在 `web/`，Vite 构建产物输出到 `frontend/dist/`，由后端静态服务托管。
- 前端同源调用 `/config`、`/v1/captures`、`/v1/captures/{id}`、SSE events 和 artifact 下载接口。
- 存储是项目级文件存储：`workspace/captures/<capture_id>/capture.json` 和 `workspace/artifacts/<capture_id>/...`。
- 后端主心智只围绕 `Capture`，不要扩展回 `AnalysisRun / Conversation / Agent / Workflow`。

## 平台与结果规则

- 支持平台：哔哩哔哩、小宇宙、抖音、小红书、微信公众号图文。
- 默认文本优先级：平台字幕、页面正文、本地或远端转写。
- 小宇宙是特例：正式正文只认 transcript，页面 shownotes / description 只作为辅助信息。
- 命中字幕或正文快路径时，应跳过转写。
- 双语字幕默认优先原语种，不默认优先翻译语种。
- 高波动平台优先公开链路，失败后再尝试项目级持久浏览器会话。
- 错误必须结构化收口，失败页和处理中页面不要暴露 raw exception、原始长链接、来源 ID 或技术细节。
- 视频预览优先 `preview_media`，下载始终使用 `source_media`。
- 图文图片展示采用“本地优先、远端兜底”；本地 artifact 未就绪时可先展示源图。

## 环境与目录

- 不污染宿主机环境，优先使用项目级依赖、缓存和运行时目录。
- Python 优先使用仓库已有 `.venv` 和 `requirements.txt`，不要擅自重建环境或升级依赖。
- 默认运行时目录：
  - `.venv`
  - `workspace/runtime/models`
  - `workspace/runtime/playwright-browsers`
  - `workspace/runtime/ffmpeg`
  - `workspace/runtime/browser-profile`
  - `workspace/captures`
  - `workspace/artifacts`
  - `workspace/temp`
  - `workspace/logs`
- `frontend/dist/` 是构建产物，不要手工编辑。
- `tests_runtime/` 和 `workspace/temp/` 是临时产物目录，可按需清理。
- `Assets/Models/...` 是 legacy 模型兜底目录，未完成迁移前不要直接删除。

## 启动、Docker 与验证

- 本地启动前应确认目标端口未被旧服务占用；默认端口是 `8000`，可用 `AUDIO2TEXT_API_PORT` 临时切换。
- Docker 构建会在镜像内构建前端，不依赖宿主机已有 `frontend/dist`。
- Faster-Whisper 模型默认不内置在镜像里；Docker 运行时必须挂载模型目录，或改用 `openai_compatible` 转写配置。
- `workspace` 挂载会覆盖容器内同路径内容；如果依赖 Playwright 浏览器运行时，需要确认挂载后的 `workspace/runtime/playwright-browsers` 可用。
- 最小验证优先级：
  - `.\.venv\Scripts\python.exe -m scripts.verify`
  - 本机 CUDA 改动后加跑 `.\.venv\Scripts\python.exe -m scripts.verify --backend-only --transcribe-smoke-file <sample.wav>`
  - `.\.venv\Scripts\python.exe -m scripts.doctor`
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`
  - `cd web && npm exec tsc -- --noEmit`
  - `cd web && npm run build`
  - `docker build -t praxis-audio2text .`（需要 Docker 可用时）
  - Browser Use 人工检查首页、处理中、结果页、下载、失败态和刷新恢复。

## 本机迭代、安装与发布协议

### 本机迭代与安装版闭环（2026-10-11）

- 用户授权批次后自主检查、评测、审查及修复，优先采用核查过的成熟实现。准确率必须有人工参考；不同模型/不同权重的 CPU 与 GPU 耗时不用于宣称纯 CUDA 加速。
- 当前 CUDA 路线是 SenseVoice Small FP32 + 官方 sherpa-onnx 1.13.4 CUDA 12/cuDNN 9 SDK。CPU 兼容版原权重为 WSYue 微调 int8，二者不可混称相同权重。Whisper 为可选备用，不因某一引擎能跑 CUDA 就替换默认模型。
- GPU SDK 通过 `scripts.prepare_sensevoice_cuda` 固定 revision 与 SHA-256 下载；不得覆盖项目 CPU venv。GPU 与 CPU ONNX DLL 必须进程隔离，worker 复用后端 `--sensevoice-gpu-worker`；显式 CUDA 不静默退回 CPU。
- 桌面打包必须使用项目 CPython 3.11，与 cp311 GPU SDK ABI 对齐。`scripts/build_desktop_sidecar.ps1` 固定该 Python 与 PyInstaller 版本，禁止 uvx 自动选择最新 Python。
- 源码、前端构建、后端 sidecar、桌面 exe 和安装包是一批交付。完成必须包含安装前备份、真实已安装 exe/sidecar 哈希核对、真实 CUDA 推理、历史正文保留，以及新功能实际验收；仅生成安装包不算用户已升级。
- 先运行 `scripts.verify --backend-only`、TypeScript、前端构建及浏览器回归，再运行 `scripts.verify_desktop_upgrade` 验证打包后的 CUDA 文件上传、模型检测、保守校对与两版下载。私有测试 API 仅在本机回环地址提供。
- 设置与校对接口限本地，复用桌面 token；密钥用 Windows DPAPI 加密，不返回明文。校对不得覆盖原 Capture，候选默认不选中，逐处确认后保存独立文件；模型不能保证语义正确，人工确认步骤不得省略。
- WebView2 的 Tauri 页面请求本地后端会带 `Sec-Fetch-Site: cross-site`，只有桌面运行目标及精确 Tauri origin 可通过此检查，桌面 middleware 的进程 token 仍必须有效。安装验收必须运行真实 WebView 窗口；普通浏览器或 HTTP 测试不能替代。
- 平台浏览器导航的超时参数不可跨函数误用：小红书使用已有 `BROWSER_GOTO_TIMEOUT_MS`，抖音使用调用方提供的 `goto_timeout_ms`。实链验收需走公开链路缺少数据后的浏览器分支，不可只 mock adapter 的成功结果；项目会话与安装版会话分别验证，不把网络风控计为抓取成功。
- Tauri NSIS 打包会把主 exe 中的 `__TAURI_BUNDLE_TYPE_VAR_UNK` 改为 `NSS`；比较未打包主 exe 与安装版时仅允许该精确标记差异，后端 sidecar 必须逐字节一致。不要把打包标记变化误报成旧程序，也不要忽略其他差异。
- 保留原品牌与线上已认可的视觉比例。2026-10-11 用户明确拒绝结果页扩宽：桌面外层宽度上限 1100px、正文/媒体工作区宽度上限 980px，整组结果高度上限 820px 并垂直居中，Web 与原生窗口相同；不得因大屏自动扩到 1720px，也不得仅收窄宽度而保留卡片撑满高窗口。验收必须覆盖真实最大化原生窗口的宽高比例。首页设置入口位于输入工具区，以 44px 点击区域的小图标呈现；结果页通过“校对 → 配置模型”进入相关配置，不放全局悬浮设置按钮。局部组件使用独立 `local-tools.css`，构建产物不手工编辑。
- 输入工具区内的设置按钮打开的弹窗必须通过 portal 放在表单之外，防止在模型/API 输入框按 Enter 时误提交首页链接；回归需验证弹窗 Enter 不触发表单、Escape 恢复按钮焦点。
- 可复核速度、样本边界、安装验证与回滚位置记录于 `docs/local-cuda-proofreading-plan.md` 和 `docs/local-upgrade-verification-20261011.md`。公开网站/Release 不随本地更新自动变更，不宣称尚未发布的远端已同步。

### 配置、校对与发布协议（2.3 批次）

- `PATCH /v1/local/preferences` 分别保存 `proofreading` / `transcription`；保留旧 PUT 兼容。AI 保存不验证或更改 ASR，ASR 保存不替换 AI 设置。
- `POST /v1/local/proofreading-check` 只发送合成短示例，验证未保存草稿，不写设置。“已保存”“模型列表可读”“实际校对测试通过”是不同状态。
- DeepSeek 官方 host 使用 JSON object、显式关闭 thinking、有限输出预算；空输出、截断、授权、余额、超时和格式错误分别返回安全错误码。不得记录服务原始正文、密钥、待校对文本；本地路由 422 不回显输入。
- 长文进度文件 `proofreading-job.json` 按块保存；来源 hash、协议、模型、地址摘要与不透明 credential revision 必须一致才可恢复。取消必须幂等，迟到结果不得覆盖新任务。
- 接受修正必须提交匹配的 `job_id`；旧窗口的过期索引不能应用到新任务。建议默认不选中，保存独立 `proofreading.json`，Capture 原文保持不变。
- 本机统一升级入口为 `scripts/upgrade_local.ps1`。需要匹配的安装包 SHA-256 与打包验收报告，停止空闲应用后备份程序、设置与历史元数据；核对完整 sidecar 文件树、主 exe 精确 NSIS 标记及历史哈希。程序、元数据回滚位置写入 upgrade-manifest。
- CI 采用 CPython 3.11 创建项目 `.venv`，通过统一 `build_desktop.ps1` 构建。CI 只上传候选 artifact，不创建公开 Release；Pages 不因候选构建成功而更新。
- 最终冻结后的安装包必须重新进行实际安装验收。发布同一份文件，官网下载再核对 SHA-256。缺 60 链接 / 72 段四小时三轮人工参考证据或干净 Windows 验收时，不伪报正式发布完成。
- 每次交付分别记录源码、已安装应用、公开 Release、官网状态。此批次实施报告：`docs/release-2.3.0.md`。
- 用户指定沿用桌面原圆点图标。应用、官网、favicon、分享图与演示统一使用 `web/public/brand-mark.svg` 的 `praxis-dot`，禁止改为“文”字候选。用 `scripts/generate_brand_assets.mjs` 同步图标；分享图单独生成，不覆盖已验收演示录像。
- 交互精修保持认可的品牌与宽高比例。等待操作显示具体动作，不能仅禁用控件；转写草稿先保存再验证 CUDA。校对进度采用单次请求完成后再安排下一次的轮询，取消或更新任务使旧响应失效；保存修正期间锁定勾选。设计与浏览器证据见 `docs/interaction-refinement-20261012.md`。
- 打包生成冻结源码、版本与主程序/后端/安装包哈希清单；本机升级必须消费并核对清单，执行保留且再次验哈希的安装包副本。安装目录的所有已有父链及目录树不得有 junction/symlink；NSIS 对空目标也检查父链和运行进程，禁止静默结束用户任务。
- 取消校对必须携带目标 `job_id`，锁内检查当前任务身份；旧任务返回 409，重复取消同一任务幂等。完整 sidecar 相对路径与 SHA-256 树摘要同时绑定 sidecar manifest、delivery manifest 和打包验收报告；仅核对 exe 不允许复用资源或安装。提取验收率必须从逐条记录重新计算，矛盾摘要拒绝，失败和风控保留在分母；当前五个支持平台每个平台每轮至少有一个完整成功样本才允许发布；仍要求至少 60 条独立链接、三轮及零测试超时。旧六平台报告保留全部失败，不移除历史行或重新包装为新范围通过。
- Windows hosted CI 常以管理员运行，WebView2 会忽略环境变量/HKCU 的调试参数；仅在受限的全新 CI runner 上临时设置并恢复 `wanxiang-chengwen.exe` 专用 HKLM 调试策略，不修改正式应用或本机策略。
- 原生退出验收只对目标子进程的唯一 `Tauri Window` 发送关闭请求，随后确认退出码。`Tao Thread Event Target` 即使被 Win32 标为可见，也不是产品窗口；不得按 PID 遍历关闭所有可见窗口。DPI 与视口分别从真实窗口和 WebView 读取，不能用浏览器模拟 DPI 宣称系统 DPI 验收。
- 后端构建指纹以 ordinal 顺序排列路径并规范化分隔符，不能依赖 `Sort-Object` 的区域/PowerShell 版本排序。父脚本与 Windows PowerShell sidecar 子脚本必须给出相同指纹；单文件变化必须使指纹失效。
- 2026-10-13 用户明确移除 YouTube 支持和宣传；新输入在 DNS/短链请求前拒绝，也不可回退通用网页或重启 YouTube adapter。已有结果与原媒体保留。不得新增 VPN、代理端口、Cookie 或登录配置。历史 YouTube 诊断和基准仅作历史证据，不纳入当前能力宣传。移除宣传须同时检查 README/官网截图及演示录像中的可见文字，不能只搜索 HTML/Markdown；素材修改必须运行 `tests.test_brand_and_showcase` 与官网回归，保持已约定的 1920×1080、30 fps、12 秒演示规格。
- 小红书匿名分享需兼容桌面 `note.noteDetailMap` 与手机 `noteData.data.noteData`，复用媒体规范化；保留完整分享参数，不把登录空壳认作正文。手机分享直连仅做一次备用 UA，逐块检查读取预算及页面大小；云端 relay 不能凭本机证据宣称已通过。定向链接、图片本地就绪与打包复验分别记录，见 `docs/xiaohongshu-mobile-20261021.md`。
- 公开截图须逐张视觉复核，不能用文本文档扫描或演示视频通过替代。2026-10-21 独立复核发现旧手机首页 PNG 仍含 YouTube 文案，已用当前前端和空示例历史重新截图；已审查图片哈希由品牌回归锁定，替换图片时必须重新复核。当前五平台三轮结果见 `docs/platform-benchmark-20261021.md`，137/180 完整、4 次超时，正式发布门槛仍未通过。

- 修改前先读相关代码、配置、文档和测试，理解现状后再动手。

## 云端预览

云端预览经 Cloudflare Worker 中继访问国内平台（`scripts/cf_worker_proxy.js`，`DEPLOYMENT_MODE=cloud_preview` 时启用）。部署身份、URL 与原理见 [`docs/history.md`](docs/history.md) 和 [`docs/deployment.md`](docs/deployment.md)。

# AGENTS.md

本文件写给后续 AI 编程助手 / Codex。它不是产品宣传稿，只记录当前仓库已经落地、需要遵守的工作规则。

## 工作方式

- 修改前先读相关代码、配置、文档和测试，理解现状后再动手。
- 默认最小改动，优先复用已有结构、命名、类型、脚本和目录约定。
- 不为了“优化”而大面积重构，不擅自引入新依赖、改 API contract、改产品方向或重做视觉风格。
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

- 支持平台：YouTube、哔哩哔哩、小宇宙、抖音、小红书、微信公众号图文。
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
  - `.\.venv\Scripts\python.exe -m scripts.doctor`
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`
  - `cd web && npm exec tsc -- --noEmit`
  - `cd web && npm run build`
  - `docker build -t praxis-audio2text .`（需要 Docker 可用时）
  - Browser Use 人工检查首页、处理中、结果页、下载、失败态和刷新恢复。

## Cloudflare Worker 中继架构

HF Space 部署在海外（非中国 IP），无法直接访问小红书/抖音等中国平台。通过 Cloudflare Worker 实现双向代理解决 GFW 问题。

### 部署账号

- **Hugging Face**: 用户名 `liamgrant`
- **Cloudflare**: 邮箱 `liamgrant.ai@gmail.com`, Account ID `f375f33442fa409ea1a9f04a4da83745`
- **Worker 脚本**: `scripts/cf_worker_proxy.js`, 名称 `wanxiang-chengwen-proxy`

### 服务 URL

| 用途 | URL |
|------|-----|
| 用户入口 (自定义域名) | `https://wanxiang.praxisai.online` |
| HF Space (后端源站) | `https://liamgrant-wanxiang-chengwen-preview.hf.space` |
| Worker 中继 (反向代理 + 转发) | `https://wanxiang.praxisai.online` |
| Worker 转发接口 | `https://wanxiang.praxisai.online/__proxy__?url=<encoded_url>` |

### 工作原理

1. **Worker 双模式**:
   - **反向代理模式**: 所有非 `__proxy__` 请求透明转发到 HF Space，解决 hf.space 域名被 GFW 屏蔽的问题。
   - **转发接口模式**: `__proxy__?url=...` 接收 URL 参数，从 Cloudflare 边缘节点（香港/新加坡）抓取目标页面，返回 HTML 和 JSON 响应。解决 HF Space 无法访问中国平台的问题。

2. **后端 relay 逻辑** (`app/browser_provider.py`, `app/extractors.py`, `app/source_adapters.py`):
   - `DEPLOYMENT_MODE == "cloud_preview"` 时自动启用 relay
   - `DEPLOYMENT_MODE == "local"` 时走直连（本地机器有中国 IP）
   - `CN_PLATFORM_PROXY` 环境变量可覆盖 relay URL（用于自定义 HTTP 代理）
   - HTTP 层: `_relay_fetch_html()`, `_relay_fetch_json()` 通过 Worker 转发 URL
   - Playwright 层: `_setup_playwright_relay_routing()` 拦截浏览器请求并路由到 Worker

3. **Worker 部署命令**:
   ```bash
   npx wrangler deploy scripts/cf_worker_proxy.js --name wanxiang-chengwen-proxy --compatibility-date 2026-05-14
   ```

4. **Worker 配置** (`scripts/cf_worker_proxy.js`):
   - `TARGET_HOST`: HF Space 域名
   - CORS 头自动添加，支持跨域访问

## 维护日志 / Maintenance Log

### 2026-05-15 维护记录

三轮迭代，当前 commit `7492049`。所有改动已验证通过（122 tests OK, TS 无错误, 前端已构建）。

**前端体验**
- 移动端剪贴板优化：Clipboard API 失败时引导长按/系统粘贴（`App.tsx`）
- 错误净化：20+ 技术术语过滤规则，前后端双重拦截（`api.ts`、`ProcessingStage.tsx`）
- 移动端布局：textarea 对称 padding，清除按钮 absolute 不受影响（`styles.css`）

**图片提取（`browser_provider.py`）**
- 尺寸阈值 160px → 120px，新增 `video[poster]` / `source[srcset]` 提取
- 小红书 Live Photo 优化：优先已知字段再递归搜索

**免费 Whisper（`cf_worker_proxy.js`、`wrangler.toml`、`transcription.py`）**
- CF Worker `/__transcribe__` 端点，Workers AI `@cf/openai/whisper`
- `cloud_preview` 模式自动选择 `CloudflareWorkersAIWhisperProvider`

**结果页信息架构**
- 布局 3 层：hero（pill + 标题 + 描述）→ 内容区（文字 + 图片/视频）→ meta footer（平台 | 类型 | 时长 | 查看来源）
- 删除 `outcome-strip`（工程指标：正文已就绪 / N个下载项 / 质量较高）
- 删除 `facts-strip`（信息与 hero/meta footer 重复）
- 实现"整理正文 / 原文"标签切换（`views.primary` ↔ `views.notes`），无 notes 时原文禁用

**UI 细节（`styles.css`）**
- 完成 pill：灰色调，与品牌一致
- 图片卡片 hover：`translateY(-3px)` + 双层柔和阴影
- 文字卡片：双层阴影、微斜渐变、边框透明度 0.06
- 正文排版：行高 1.95、段落间距 18px、颜色 `#2c2c2c`

**Bug 修复**
- `source_adapters.py` 补充遗漏导入 `DEPLOYMENT_MODE`、`CN_PLATFORM_RELAY_URL`

**Apple HIG 合规（第 4 轮）**
- 删除死 CSS 9 组：`.header-note`、`.status-chip.is-ready/.is-danger`、`.hero-kicker`、`.hero-trust-grid`、`.processing-ticker`、`.processing-progress` 系列、`.image-gallery-body.is-previewing`、`.image-card-action.is-primary`
- 触控目标 ≥ 44pt：`--action-height` 42→44、`.image-card-action` 36→44、`.image-viewer-close` 42→44、`.image-viewer-nav` 38→44
- 文档精简：三轮维护日志合并为一个条目，去除重复表述

**已知风险**
- CF Workers AI Whisper 免费版日配额限制，超大音频可能超时
- Live Photo 抓取成功率依赖平台接口稳定性

### 2026-05-15 第 5 轮：8pt 网格对齐 + 文案微调（commit `0785777`）

**8pt 网格间距对齐（`styles.css`，约 90 处修改）**
- gap 值：`5/6/10→8px`、`12/14→16px`、`18→24px`、`26→24px`
- Section padding：`.main-stage` 18/26→24/24、`.processing-shell` 34/28/30→32/32/32、`.hero-form-card` 18/20→24/24、`.result-prose` 22/28→24/32
- 组件尺寸：`.brand-mark` 28→32、`.hero-submit` 52→56、`.image-card-badge` 58/30→56/32、`.image-viewer-toolbar-button` 34→32
- 移动端同步对齐：640px / 960px 断点内 margin/padding/border-radius 校正
- 微间距保留：2-4px 的视觉微调值（Apple 允许）

**文案微调**
- placeholder `贴入`→`粘贴`（`InputStage.tsx`）
- 视频不可用面板：`暂未拿到可预览视频`→`视频预览暂不可用`（`DeliverableStage.tsx`）

**清理**
- 删除 15 个临时调试文件（截图、快照日志、测试 HTML），释放约 3MB

### 2026-05-15 第 6 轮：去标签化 + 测试全绿 + 上线准备

**移除整理正文/原文标签切换**
- 删除 `result-mode-tabs` 组件及相关 CSS（桌面+移动端，共 ~50 行）
- 删除 `textViewMode` 状态、`hasNotesText`、`notesText` 逻辑、`TextViewMode` 类型
- 结果页直接展示 `primary_text`，不再有二级视图切换
- `DeliverableStage.tsx` 简化约 15 行

**Vite 开发配置**
- 新增 `server.proxy`：`/config` 和 `/v1` 代理到 `http://127.0.0.1:8000`（`vite.config.ts`）
- dev server 可以直接调后端，无需额外配置

**测试验证**
- 122 后端测试全部通过（unittest discover），1 跳过（live smoke 需配置 URL）
- TypeScript 零错误，Vite 构建通过（CSS 38.84KB, JS 322.35KB）
- UI smoke `test_video_result_layout_stays_stable` `.facts-strip` 引用已修复为 `.deliverable-meta`
- hover 测试使用 `force: true`（标准 hover 效果测试模式）

**文档同步**
- README.md、docs/verification.md、docs/release-ready.md 中 "details row" / "facts row" 统一更新为 "meta footer"

### 2026-05-15 上线前最终闭环

**测试验证**
- 后端测试：122 passed / 1 skipped
- TypeScript：零错误
- 前端构建：成功（CSS 38.93KB, JS 322.39KB）

**功能冒烟**
- 首页：通过（品牌标识、输入框、按钮、最近记录均正常）
- 处理中页面：通过（进度条、状态文案、处理中标签正常）
- 结果页（视频）：通过（hero区、双栏布局、视频播放器、meta footer齐全）
- 结果页（图文-小红书）：通过（来源识别成功，提取失败时错误提示友好）
- 失败态：通过（处理失败pill、友好文案、无技术细节暴露）
- 移动端适配：通过（响应式布局、按钮堆叠、文字换行正常）
- 图片查看器：未测试（本地无图文完整结果，需线上验证）

**代码质量**
- 死代码清理：无（CSS/TSX/Python 均无未使用代码）
- 文档一致性：无（README.md、docs/ 均无过时引用，CSS类名与TSX完全对齐）

**结论**
- 是否可以上线：是
- 遗留风险：CF Workers AI Whisper 免费版日配额限制，超大音频可能超时；Live Photo 抓取成功率依赖平台接口稳定性；图片查看器未在本次冒烟中覆盖（需线上图文结果验证）

### 2026-05-15 部署记录

**已部署**
- HF Space: `https://liamgrant-wanxiang-chengwen-preview.hf.space`（海外可访问）
- CF Worker: `wanxiang-chengwen-proxy`（已部署，功能正常）
- 自定义域名: `wanxiang.praxisai.online`（已绑定 Worker，`.online` TLD 中国 DNS 可解析）
- 旧域名: `praxisai.men`（已绑定 Worker，但 `.men` TLD 国内不可用）

**国内访问**
- `wanxiang.praxisai.online` 已验证中国 DNS（阿里 223.5.5.5、腾讯 119.29.29.29）可正常解析
- `.online` 是主流 TLD，不会被 GFW 识别为代理域名

**当前可用地址**
- 国内/海外: `https://wanxiang.praxisai.online`
- HF Space 直连: `https://liamgrant-wanxiang-chengwen-preview.hf.space`
- Worker 默认域名: `https://wanxiang-chengwen-proxy.liamgrant-wanxiang.workers.dev`

**额度限制（已实现）**
- 每日次数：12 次/IP/天（环境变量 `DAILY_CAPTURE_LIMIT`）
- **小红书图文免限流**：无转写，不消耗计算资源，不计入每日次数
- 视频时长：≤ 30 分钟（环境变量 `MAX_VIDEO_DURATION_MINUTES`）
- 文件大小：≤ 200MB（环境变量 `MAX_UPLOAD_SIZE_MB`）
- 管理员 IP 免限制：环境变量 `ADMIN_IPS`（逗号分隔）
- 用户隔离：基于客户端 IP（`X-Forwarded-For` / `CF-Connecting-IP`），每个 IP 只能看到自己的历史记录

### 2026-05-16 架构简化 + Bug 修复

**根因修复（4 个）**
- 剪贴板粘贴：`execCommand('paste')` 移到 `await` 之前同步执行，保留用户手势（`App.tsx`）
- Docker Chromium：加 `--no-sandbox --disable-gpu` 启动参数（`browser_provider.py`）
- Worker Content-Type：保留原始 Content-Type，不再强制 `text/plain`，SPA 可正常渲染（`cf_worker_proxy.js`）
- 小红书页面检测：补充 "你访问的页面不见了" 等 404 标题匹配（`source_adapters.py`）

**架构剃刀**
- 小红书提取：跳过 HTTP 中继静态抓取（SPA 空壳永远无数据），直接 Playwright + `page.evaluate()` 从 `window.__INITIAL_STATE__` 提取笔记 JSON。不再传输 `page.content()` 全文（521KB → 2KB），总耗时减少 30-40%（`browser_provider.py`）
- 前端错误处理：砍掉 55 行 `RAW_TECH_PATTERNS` 正则黑名单，后端已产出中文错误信息，前端直接透传（`api.ts`）
- 小红书限流豁免：`_check_daily_rate_limit` 移至 URL 解析后，按平台判断（`main.py`）

**已部署**
- GitHub: clean-main 分支
- HF Space: `liamgrant/wanxiang-chengwen-preview`
- Cloudflare Worker: `wanxiang-chengwen-proxy`（Content-Type 修复）
- 自定义域名: `wanxiang.praxisai.online`

### 2026-05-16 图片查看器工具栏 + 跨平台字体修复

**根因修复（2 个）**
- 工具栏膨胀：移动端 `.image-viewer-toolbar-group.is-navigation` 的 `flex: 1 1 100%` 强制占满整行，把图片工具组和下载按钮挤到额外行，工具栏从 2 行膨胀到 3 行（166px），遮挡图片（`styles.css`）
- 跨平台字体差异：WeChat WebView 可能自动调整文本大小，导致字体渲染与 Safari 不一致（`styles.css`）

**修复内容**
- 移动端工具栏重排：导航组 `flex: 0 1 auto`，图片工具组 `flex: 1 1 auto`，下载按钮 `order: 1` 独占末行；按钮尺寸 44→40px，间距 8→6px，圆角 26→20px
- 工具栏高度从 166px（3 行）降至 100px（2 行），不再遮挡图片
- PC 端单行布局不受影响（桌面断点未修改）
- 根元素添加 `-webkit-text-size-adjust: 100%` + `text-size-adjust: 100%`，阻止 WeChat WebView 自动放大文字

**验证**
- 后端测试：122 passed / 1 skipped
- TypeScript：零错误
- 前端构建：CSS 37.93KB, JS 322.32KB
- Playwright 截图验证：WeChat iPhone (375×812)、Safari iPhone (390×844)、Narrow Android (360×780)、Desktop (1440×900) 四端工具栏布局正确

**已部署**
- GitHub: clean-main 分支
- HF Space: `liamgrant/wanxiang-chengwen-preview`

### 2026-05-16 Apple HIG 合规 + 移动端布局全面优化

**触控目标修复（3 个，≥ 44pt）**
- `.hero-input-clear`：40→44px（清空输入按钮）
- `.recent-inline-delete`：32→44px（删除记录按钮）
- `.image-viewer-toolbar-button`：36→44px（图片查看器工具栏按钮）

**工具栏布局统一**
- `image-tools` 组 `flex: 1 1 auto` → `flex: 0 1 auto`，不再膨胀挤占空间
- 普通图和 Live 图工具栏结构一致：导航+缩放在同一行，下载在第二行
- 按钮 44→36px（桌面）/ 44px（移动端），间距统一 4-6px

**正文区间距消除**
- `.result-content-shell` 移动端 `flex: 1` → `flex: 0 0 auto`，不再撑满剩余空间
- `.result-prose` 移动端 `min-height: 100%` → `auto`，内容自适应高度

**跨平台字体一致性**
- 根元素 `-webkit-text-size-adjust: 100%` + `text-size-adjust: 100%`，阻止 WeChat WebView 自动放大

**验证**
- 后端测试：122 passed / 1 skipped
- TypeScript：零错误
- 前端构建：CSS 37.98KB, JS 322.32KB
- Playwright 四端截图：WeChat iPhone、Safari iPhone、Narrow Android、Desktop 全部布局正确

**已部署**
- GitHub: clean-main 分支（`e708c61`）
- HF Space: `liamgrant/wanxiang-chengwen-preview`

### 2026-05-16 桌面端两栏布局修复

**根因**
- 提交 `d6c59f5` 移除了 `@media (min-width: 1025px)` 内的两栏网格布局，改为所有宽度单栏自然流
- 原因是桌面端 `height: 100vh` 高度锁定导致部分视口文案列被挤压至 ~25% 宽度
- 修复方案过于激进，未区分桌面/手机断点，导致桌面端 UI 崩坏为手机端样式

**修复内容**
- 恢复 `body.has-result-stage` 高度锁定链：body → #root → .app-shell → .main-stage → .stage-shell-ready
- 恢复 `.stage-shell-ready` 高度计算 `calc(100vh - 96px)` + `overflow: hidden` 级联
- 恢复两栏网格：`.deliverable-workspace.has-images` → `grid-template-columns: minmax(0, 1.06fr) minmax(320px, 0.94fr)`
- 恢复 `.deliverable-workspace.has-video` → `grid-template-columns: minmax(0, 1.06fr) minmax(360px, 0.94fr)`
- 恢复面板内部滚动：`.result-content-shell` / `.image-gallery-body` 各自 `overflow: auto`
- 手机端断点（≤960px / ≤640px）未触碰

**验证**
- 前端构建：CSS 39.22KB, JS 322.32KB
- Playwright 截图验证：Desktop (1440×900) 两栏布局正确，Mobile (390×844) 单栏布局正确

**已部署**
- GitHub: clean-main 分支（`607226f`）
- HF Space: `liamgrant/wanxiang-chengwen-preview`

### 2026-05-17 UI 回归修复 + 备份版本对齐

**根因**
- 多次增量修改导致 CSS 和组件代码与备份版本不一致
- 组件使用 `deliverable-meta` 类名，但 CSS 已切换为 `fact-chip` 类名
- `viewer-media-enter` 动画中的 `transform: scale()` 与图片查看器交互式 transform 冲突

**修复内容**
- 恢复备份版本的 CSS（`styles.css`）作为基础
- 恢复备份版本的组件（`DeliverableStage.tsx`）作为基础
- 仅叠加必要改动：hero 标签样式、`-webkit-text-size-adjust`
- 移除冲突的动画 transform（只保留 opacity 淡入）

**关键教训**
- 备份版本的类名体系（`fact-chip`、`facts-strip-side`）与之前版本不同
- 修改 CSS/组件时必须同时检查类名匹配
- 动画中的 `transform` 会覆盖元素的交互式 `transform`，导致布局异常

**验证**
- 前端构建：CSS 41.30KB, JS 323.20KB
- Playwright 截图验证：Desktop 居中正确，两栏布局正常

**已部署**
- GitHub: clean-main 分支
- HF Space: `liamgrant/wanxiang-chengwen-preview`

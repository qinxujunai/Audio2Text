# 2026-10-22 本机三轮与后台原视频复核

本次由用户明确授权执行同一批样本重测。输出目录按用户要求命名为 `tests_runtime/product-upgrade/benchmark-20261023/`，测试实际在 2026-10-22 执行。下述源码、基准和安装状态分别记录。

## 同步与测试来源

- GitHub 候选同步到 `42ba8a55fb17ea1a16d4dd06e8206d8bff87d61a`，`clean-main` 同步到 `2ceaab7d6d0c6f6eb1ebce43eb6aa92a0d1eba82`。
- 本地备份 `backup/local-20261022` / `82745d0` 保留原 HEAD `6c417b9` 的历史、未跟踪计划、品牌候选和工具项目配置，没有合回候选分支。最初显示修改的四个代码文件在 `git add` 后与 HEAD 内容一致，未形成独有代码 diff。
- 同一份 60 条私有 manifest，SHA-256 `24ee68e5536e04a32cd92a2e971f46705f500d1c75f64a31a42b7782e707c7f2`，核对 60 个独立 canonical 身份和五平台。
- 基准全程使用 `42ba8a5` 的 `app/`、`scripts/` 代码；后台画质修复仅在 180 次全部结束后应用。提取计时不含转写或后台原视频下载，行内 `video_deferred` 保留该边界。

## 三轮结果

| 平台 | 完整 / 尝试 |
| --- | --- |
| 哔哩哔哩 | 57 / 57 |
| 小宇宙 | 27 / 30 |
| 抖音 | 24 / 30 |
| 小红书 | 3 / 33 |
| 微信公众号 | 30 / 30 |
| 合计 | 141 / 180 |

零 `benchmark_timeout`，每样本上限仍为 180 秒；五个平台每轮都至少一个完整成功。完整率 78.33%，p50 3.914 秒、p95 15.480 秒。39 次失败保留在分母，不能称为 180 次全部成功。

`scripts.validate_benchmark_reports` 从原始行校验后 `passed: true`。准确率证据复用既有、未改变权重的 `sensevoice-all-slices-evidence.json`：6098 段、6.082057 小时、三轮，`ready: true`；本次没有重新执行准确率大样本，也没有更换转写模型。

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_extraction --manifest tests_runtime/product-upgrade/xhs-mobile-20261021/current-five-platforms.jsonl --repeat 3 --sample-timeout-seconds 180 --json-out tests_runtime/product-upgrade/benchmark-20261023/extraction-report.json
.\.venv\Scripts\python.exe -m scripts.validate_benchmark_reports --extraction tests_runtime/product-upgrade/benchmark-20261023/extraction-report.json --transcription tests_runtime/product-upgrade/sensevoice-all-slices-evidence.json
```

## 失败逐项说明

每行样本的第 1、2、3 轮都失败；原始报告保留所有 39 条，逐条脱敏索引为本地 `failure-evidence.json`。没有删除或替换链接，也没有添加 Cookie、登录或代理配置。

| 样本 | 三轮错误码 | 证据与原因边界 |
| --- | --- | --- |
| public-xiaoyuzhou-1 | source_not_found | 来源未提供可用内容；不能把不可读取来源算成功 |
| public-douyin-4 | douyin_fresh_cookies_required | 应用 provider 链最终错误；定向分享 API HTTP 200 / status_code 0，但详情为空，video 过滤 reason 7、note 过滤 reason 8 |
| public-douyin-6 | douyin_fresh_cookies_required | 同上，不能由最终错误文案推断登录一定能恢复；未验证删除或私密的具体语义 |
| public-xiaohongshu-1 | source_not_found | 旧链接无 xsec_token，当前公开链路不可读取；不是有效完整分享样本 |
| public-xiaohongshu-2 | source_not_found | 同上 |
| public-xiaohongshu-3 | source_not_found | 同上 |
| public-xiaohongshu-4 | source_not_found | 同上 |
| public-xiaohongshu-5 | source_not_found | 同上 |
| public-xiaohongshu-6 | source_not_found | 同上 |
| public-xiaohongshu-7 | source_not_found | 同上 |
| public-xiaohongshu-8 | source_not_found | 同上 |
| public-xiaohongshu-9 | source_not_found | 同上 |
| public-xiaohongshu-10 | source_not_found | 同上 |

抖音成功对照 `public-douyin-1` 的 video 分享接口返回一条完整详情。上游 [parse-video-py](https://github.com/wujunwei928/parse-video-py/blob/main/src/parse_video_py/parser/douyin.py)（MIT，核查时未归档，最近推送 2026-08-19）使用相同优先链；没有恢复已停用 iteminfo。此次用户指定的旧失败样本抽查仍失败，不能用成功对照替换其结论。

## B 站真实应用与画质根因修复

`current-bilibili-7` 全新 Capture，视频约 2804 秒，源码应用按本机已有配置使用 Faster-Whisper CUDA；该次耗时不等同安装版 SenseVoice 的性能。

- 文字首次可用：117.823 秒。
- 初次后台视频首次可用：从提交起 199.186 秒，即文字后 81.363 秒。
- 浏览器实际出现原视频下载、音频下载和播放器；播放推进，无 JavaScript 错误。
- 进一步 ffprobe 发现后台 DASH 视频只有 854×462，而旧验收渐进式原视频是 1280×692。`complete_deferred_bilibili_video` 跳过了原先渐进式优先链，这是画质回退，不能据较小文件的速度宣称保持原视频质量。

修复 `f7b9b33`：文字仍由音轨优先交付；仅后台恢复渐进式优先，复用现有多连接下载器，渐进式失败才回落 DASH 与保存音轨合并。参考 [yt-dlp 的格式提取](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) 与 [Bili23 的多线程架构](https://github.com/ScottSloan/Bili23-Downloader)，没有引入运行依赖或 GPL/AGPL 代码。

只补跑受影响的后台流程：66.420 秒完成；259809710 字节、1280×692，H.264 视频与 AAC 音频；SHA-256 `d4245872cdde9952c771fe32bc105461b50c9f088f4379553b0d952d2545ec6e`，与此前同一样本原视频一致。正文与文字附件哈希不变，pending 清除，deferred 文件清除。66.420 秒是独立补跑的后台耗时，不能与首次 117.823 秒拼成一次新端到端实测。

## 自带检查与交付边界

- 修复前两次后端检查各 392 项：391 通过、1 条条件跳过。
- 两个新增画质回归先失败；修复后 16 项相关回归通过。
- 修复后 `scripts.verify --backend-only`：394 项，393 通过、1 跳过；doctor、Python 编译通过。
- TypeScript、Vite 构建、32 项真实 HTTP/浏览器回归通过；npm 正式依赖安全审计 0 漏洞。最初 `npm exec tsc -- --noEmit` 被本机 npm 12 参数解析拒绝，调用项目内 tsc 后通过，无需更改依赖。
- 独立只读代码审查无阻断；实际画质补验弥补 mock 测试边界。

## 2026-10-23 安装与正式发布闭环

发布冻结来源 `a192250a49258a473d13867b93d1ad8f77e51ae9`。统一构建、打包 CUDA 文件上传/校对/两版下载通过；本机统一升级入口执行后，完整运行组件树及主程序哈希核对通过。安装包 109907373 字节，SHA-256 `a4a7a62ccf597210587bf2a4f837aa78c7ad62d2c4cf9813634c91a6b3d2b1a3`。

新安装版 SenseVoice CUDA 完整处理同一样本：25.452 秒交付文字，从提交起 92.970 秒原视频就绪，文字后 67.518 秒补齐；1280×692、2803.763 秒，原生播放器实际推进且无错误。这是不同引擎配置下的新一次端到端观测，不能与前述 Faster-Whisper 测试混作纯 CUDA 加速比。

真实 WebView 通过 CUDA 推理、DeepSeek 合成短例（722 ms，单次观测）、校对弹窗和原文保存；普通/最大化窗口比例 1100/980/820 与实际 DPI 144 通过。原 10 条历史 Capture 和加密设置哈希不变，另新增一条 B 站验收记录。只向该进程唯一 `Tauri Window` 正常关闭，退出码 0，随后正常重启且 9222 调试端口关闭。

[Quality Gate 39807590791](https://github.com/qinxujunai/Audio2Text/actions/runs/39807590791)通过；[干净 Windows 同文件验收 39811228460](https://github.com/qinxujunai/Audio2Text/actions/runs/39811228460)报告 `passed: true`，安装摘要相同，首次组件、CPU 转写、原生播放、三类保存和正常退出均通过。[v2.3.0 正式 Release](https://github.com/qinxujunai/Audio2Text/releases/tag/v2.3.0)上传的是该验收文件，未用 CI 重建包替换；已核对 GitHub asset digest。官网部署与下载校验回执见项目状态。

构建过程中首先发现当前进程没有指向已存在的 Rust 安装，临时设置其 `RUSTUP_HOME` / `CARGO_HOME` 后解决，没有修改全局环境。首次 Tauri 构建规范化 `Cargo.toml` 换行，被冻结检查正确拒绝；确认无语义差异后保留冻结检查、复用完整哈希匹配且已验收的 sidecar，重新生成桌面包通过。没有绕过任何冻结或验收门槛。

本轮回滚目录：`workspace/temp/product-rollback-20261022-235642/`。安装包仍未签名。

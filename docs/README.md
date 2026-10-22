# 文档索引与接手顺序

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 项目维护文档 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

先读 [项目最新状态](project-status.md)，再读 [AGENTS.md](../AGENTS.md)。本批文档同步日期为 **2026-10-22**；这不是对历史测试的重新执行日期。

## 先确认所在分支

2.3 升级实现位于 `codex/wanxiang-platform-audio-ui`（[PR #2](https://github.com/qinxujunai/Audio2Text/pull/2)，仍为草稿）。默认分支收到的是最新文档快照，不表示应用实现已经合并。已安装本地版本为 2.3.0 候选，正式公开下载仍为 2.1.0；官网品牌维护已上线，完整发布仍受阻。

## 文档清单

| 文档 | 用途 |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [万象成文](../README.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [Security Policy](../SECURITY.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [Third-Party Notices](../THIRD_PARTY_NOTICES.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [SenseVoice CUDA 人工参考测评](ascend-accuracy-20261012.md) | 专项历史/实测证据，保留原日期 |
| [ASR 模型量化审计（2026-10-11）](asr-model-quantitative-audit-20261011.md) | 专项历史/实测证据，保留原日期 |
| [Praxis AI｜无界笃行 / 万象成文 部署说明](deployment.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [开发、部署与维护](developer-guide.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [Extraction Benchmark](extraction-benchmark.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [交互精修（2026-10-12）](interaction-refinement-20261012.md) | 专项历史/实测证据，保留原日期 |
| [本地 CUDA 与保守校对](local-cuda-proofreading-plan.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [本地升级验收 · 2026-10-11](local-upgrade-verification-20261011.md) | 专项历史/实测证据，保留原日期 |
| [当前五平台三轮提取验证](platform-benchmark-20261021.md) | 专项历史/实测证据，保留原日期 |
| [新智能体接手与发布核查](pre-launch-prompt.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [本地产品质量复核 · 2026-10-11](product-quality-audit-20261011.md) | 专项历史/实测证据，保留原日期 |
| [项目交付状态](project-status.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [六平台公开链接三轮实测](public-platform-benchmark-20261013.md) | 专项历史/实测证据，保留原日期 |
| [万象成文 2.3.0 实施与发布记录](release-2.3.0.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [Praxis AI｜无界笃行 / 万象成文 发布收口说明](release-ready.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [2.3 候选独立审查与修正](release-review-20261012.md) | 专项历史/实测证据，保留原日期 |
| [官网品牌与平台展示维护](site-maintenance-20261021.md) | 专项历史/实测证据，保留原日期 |
| [Transcription Benchmark](transcription-benchmark.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [2.2.3 本地布局修正](ui-width-repair-20261011.md) | 专项历史/实测证据，保留原日期 |
| [Praxis AI｜无界笃行 / 万象成文 验收清单](verification.md) | 当前指南或协作约定；先核对分支与最新状态 |
| [小红书匿名分享兼容修复（2026-10-21）](xiaohongshu-mobile-20261021.md) | 专项历史/实测证据，保留原日期 |
| [万象成文演示片](../showcase/README.md) | 当前指南或协作约定；先核对分支与最新状态 |

## 日期与证据规则

- 每份项目维护文档标注今天的维护日期；历史记录的原测试日期、提交、安装包身份及失败结论保留，不把它们伪装成今天重新验收。
- 保留带日期文件名以免破坏已有链接；阅读入口统一指向本索引和最新状态，不需要靠重命名判断新旧。
- 第三方原始模型卡 `Assets/Models/FasterWhisper/medium/README.md` 保留上游元数据与原文，不修改为项目今天的新测试报告。
- 本地诊断、私人正文与分享参数、密钥、草稿工作笔记不进入这份公开文档快照。
- 文档同步不恢复受阻任务，不触发长测试、打包、安装或新模型实验。

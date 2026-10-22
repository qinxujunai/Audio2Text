# 本地产品质量复核 · 2026-10-11

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 专项记录；原实测日期保留 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

本轮落实到真实桌面程序，不把视觉方案、源码测试或公开论文当成交付验收。原品牌和主布局保留。

## 设计依据与实际检查

- [Apple Materials](https://developer.apple.com/design/human-interface-guidelines/materials)：玻璃用于功能控件与导航，正文使用稳定底色，避免过度叠加。设置按钮/弹窗标题使用克制透明材质，正文和校对预览保持可读。
- [Apple Motion](https://developer.apple.com/design/human-interface-guidelines/motion)：动作反馈短而明确、可减少运动、不阻塞频繁操作。现有界面支持 reduced-motion，图片查看器也读取系统偏好；本地设置支持 reduced-transparency。
- 本轮完整 UI 回归 25 项通过，另有设置/校对两个交互测试、四种设置视口、16 种桌面结果组合与真实安装窗口验证。包括失败恢复、手机触控、图片/Live Photo、音视频、长正文、Escape、焦点及独立导出。主视觉未为追求“苹果”而换品牌。

## 技术依据与复用

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) 负责成熟平台媒体提取；实时核对上游稳定版为 2026.08.19，与 requirements 和本机一致。[EJS 官方文档](https://github.com/yt-dlp/yt-dlp/wiki/EJS) 的 Node 路径已接入。不因存在 nightly 就无证据升级所有依赖。
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 负责本地识别；采用官方 CUDA SDK，CPU/GPU DLL 进程隔离和持久 worker。固定来源和校验，保留原 CPU 环境。
- Playwright 负责项目级持久浏览器会话与公开链路兜底，已有失败分类和有限重试；不新增未知签名/登录插件，不收集系统浏览器凭据。

## 本轮平台实链证据

| 平台 | 本轮样本结果 | 提取耗时 | 证据范围 |
| --- | --- | ---: | --- |
| YouTube | 成功 | 33.066秒 | 字幕及原媒体 |
| 哔哩哔哩 | 成功 | 17.192秒 | 字幕及原媒体 |
| 小宇宙 | 成功 | 4.592秒 | 可转写音频；shownotes不冒充transcript |
| 抖音 | 成功 | 21.309秒 | 文本及原媒体 |
| 微信公众号 | 成功 | 1.361秒 | 正文及图片 |
| 小红书 | 一条成功、一条风控 | 13.494秒 / 63.197秒 | 安装版会话取回12张图片；另一条不计成功 |

前五个平台报告：`workspace/benchmarks/extraction-live-audit-20261011.json`。
小红书安装会话报告：`workspace/benchmarks/xiaohongshu-installed-profile-audit-20261011.json`。
没有把两个不同会话的网络风险结果合并为“全部通过”，也没有把一轮小样本写成平台长期成功率。

## 实测发现并修复

小红书浏览器分支误用了只在抖音函数中存在的 `goto_timeout_ms`，导航前出现 NameError；抖音自身反而使用固定超时，忽略 adapter 提供的25秒预算。修复分别恢复小红书已有常量和抖音调用参数。两项测试先复现失败再通过；308项后端运行、1项线上配置测试跳过，其余通过。平台样本走实际网络，未仅mock adapter成功。

## 交付与边界

安装版 CUDA、模型检测、保守校对、原文/校对版下载、历史保留和最终安装身份见 `local-upgrade-verification-20261011.md`。API文字模型需要用户自己的服务配置，外部模型效果未使用仿真测试伪装成实测。英文/混说尚无充分人工参考，不能给出整体准确率。

公开站点、干净新电脑与正式发布的大样本门槛没有在本批次冒充已完成。平台登录、网络风控、内容删除和签名链接有效期仍会影响可访问性；保留结构化恢复流程，并保留已有完整结果。回滚文件在 `workspace/temp/installed-rollback-20261011/`。

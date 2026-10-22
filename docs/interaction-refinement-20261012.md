# 交互精修（2026-10-12）

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 专项记录；原实测日期保留 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

本轮沿用圆点品牌图标、黑白配色、现有字体与已确认的结果页比例。只精修本地设置与校对控件，不调整首页和结果工作区的宽高。

## 设计约束

- 颜色：正文 `#111111`、次要说明 `#666666`、稳定底色 `#fafafa`、字段 `#ffffff`；蓝色 `#0071e3` 只用于键盘焦点，绿/红只表达验证结果。
- 字体沿用应用字体；标题 20px、分组 16px、说明 14px、辅助说明 12px。
- 校对配置为主要任务，运行组件保留折叠入口。模型检测放在模型字段旁；避免独占一整行的次级操作。
- 材质仅用于弹窗标题栏与背景遮罩，字段和正文保持稳定底色。按钮使用与结果页一致的胶囊轮廓，点击区域至少 44px。
- 等待必须说明操作：正在检测、测试、保存、验证、校对、取消；禁止仅禁用控件而没有状态反馈。
- 转写草稿与已保存设置不同：先保存再进行 CUDA 验证，避免把旧设置的成功解释为新设置可用。
- 校对建议保留逐处确认；保存期间不能改变勾选。新任务清空旧选择，过期轮询结果不得覆盖更新后的状态。
- 保留键盘焦点、Escape 返回、减少动态效果、减少透明度与高对比模式。

## 参考与边界

参考 Apple 的 [Materials](https://developer.apple.com/design/human-interface-guidelines/materials)、[Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback) 与 [Writing for interfaces](https://developer.apple.com/videos/play/wwdc2022/10037/)，并使用本地 `design`、`frontend-design`、`ux-writing` 技能。Windows WebView 的 CSS 材质不是 Apple 原生 Liquid Glass 实现，不以名称宣称原生效果。

已通过 TypeScript 检查、Vite 构建、5 项真实 HTTP/浏览器交互回归及 17 项品牌/构建指纹/测评工具测试。浏览器回归覆盖 390/760/1280/1920px 设置、弹窗 Enter/Escape、等待状态、转写草稿、两版导出、取消后的迟到轮询与超过 1.5 秒的慢进度响应。记录位于 `tests_runtime/product-upgrade/interaction-ui.log` 和 `interaction-contracts.log`，初始设置截图为 `settings-refined.png`。

独立复核指出慢轮询重叠、安装父目录链接及安装包源路径竞态；已修复并补测。NSIS 实际小安装器覆盖运行中拒绝、空目标拒绝运行中进程、新目标链接父目录拒绝、旧程序备份及无旧 DLL 混入；PowerShell 安全扫描的真实父/子 junction 测试通过。后端完整验证为 329 tests，1 skipped。此处的安装器夹具测试不代替真实产品安装验收。

本轮完整构建和安装已通过。真实安装窗口通过 CUDA 推理、四小时历史查看与视频播放、原文下载、最大化比例、普通 1280×820 逻辑窗口（系统 150% 缩放）、焦点返回和减少动态效果检查。当前 DeepSeek 合成校对测试 794ms，下一帧按钮反馈 1.7ms；这是单次实测，不是长期性能保证。正常启动已退出调试模式，9222 端口关闭。

安装包 SHA-256：`d3df68fddb8054c99e9262f3674a41e04c630abb3a95398b9ee51f89e634fe56`。实际主程序哈希：`b70474627dfa5fd3376b8da6e036cc500271034549d7c05dec52d13223e20f34`；后端哈希：`d5d13d9180063df648a63a94d96e30ca01791f7a85eeb537a1e1f4b357b3593a`。安装时冻结清单核验通过，程序及九条历史记录的哈希检查通过。

回滚位置 `workspace/temp/product-rollback-20261012-112304/`，包含 `validated-installer.exe`、原程序、设置与历史元数据。第一次尝试因 NSIS 延迟读取 Win32 last-error 将不存在的目录误判为不安全，自动恢复成功；修复为同次调用捕获错误并补真实 NSIS 缺目录回归后，重新冻结构建、安装、验收通过。

官网与公开 Release 仍为旧版本。这是新本地候选的安装验收，后续完整基准、干净 Windows 和最终源码快照仍需验收，不称为正式发布。

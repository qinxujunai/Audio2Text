# 2.2.3 本地布局修正

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 专项记录；原实测日期保留 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

## 问题与依据

用户指出本地结果页过宽，首页右上角新增设置按钮破坏原有美感。
2026-10-11 从实际线上入口 `https://wanxiang.praxisai.online` 获取 HTML 与 CSS：外层为 `min(1100px,100%)`，工作区为 `min(980px,100%)`。本地桌面断点额外覆盖到 1720px，并让工作区铺满外层，是两者不同的直接原因。

## 修正范围

- 恢复线上 1100/980px 居中上限，整组结果高度上限 820px 并垂直居中；保留两栏、内部滚动、原字体/品牌/圆角和移动端布局。
- 删除全局固定的“设置”按钮。首页设置图标放入已有输入工具区，点击区域 44×44px，保留可访问名称、悬停提示与键盘焦点。
- 设置弹窗通过 React portal 放在首页表单之外，避免模型/API 字段按 Enter 误提交链接。新增回归修复前失败（表单提交 1 次）、修复后通过（0 次），四个设置视口均验证。
- 结果页继续通过“校对 → 配置模型”打开配置，CUDA 选择与验证仍保留在首页设置内。
- 更新布局回归，检查实际宽度、居中位置、无横向溢出、媒体/正文等高与底部信息可见。设置回归检查新入口的位置、尺寸、弹窗、模型检测、保存、Escape 与焦点恢复。

此处采用 Apple HIG 的内容层级、控件分组与渐进展示原则，并非重新设计产品或在正文上叠加玻璃材质：
[Layout](https://developer.apple.com/design/human-interface-guidelines/layout)、[Settings](https://developer.apple.com/design/human-interface-guidelines/settings)。

## 交付与回滚

独立备份：`workspace/temp/ui-width-rollback-20261011/`，包含修改前 2.2.1 完整安装目录、安装包、九条历史元数据及本轮 UI 源码。历史媒体文件不移动、不覆盖。
本轮不部署公开网站。安装版、安装包及真实 WebView 的验证结果将保存在 `tests_runtime/ui-width-repair/`。

## 源码与候选包验证

- `scripts.verify --backend-only`：308 项运行，307 通过、1 跳过；doctor 与 Python 编译检查通过。
- TypeScript 检查、Vite 构建通过。
- 浏览器回归 27 项通过，包括 Web/原生 IPC 测试上下文下 1280、1707、1920、2560px 四个桌面宽度及 390/760px 设置弹窗；工作区 980px、左右居中、无溢出，原移动端与媒体交互保留。
- 独立候选后端 `verify_desktop_upgrade`：真实 CUDA 上传识别、模型检测、保守校对、数字修改拒绝、原文保留、两版下载与跨来源访问拒绝通过。
- 本轮生产改动仅为布局上限和设置入口位置/图标；最终版本标识更新为 2.2.3。后端业务算法没有在本轮改变。验收脚本删除了“推理前读取的设备状态必须已暖机”这一时序误判，仍断言已完成真实推理使用 CUDA。

## 最大化窗口比例补充

用户继续指出仅收窄宽度后卡片仍过长。根因是桌面 `height:100%` / 工作区 `flex:1` 保留了随窗口高度无上限伸长的行为。最初 2.2.2 的修正不完整，不作为最终视觉验收。最终限制整组结果最高 820px，并在高窗口中上下居中，让正文、视频与底部信息保持正常窗口的比例。

已在此电脑真实最大化 WebView（CSS 1707×1044，物理约2560×1566）对照预览：正文卡片约510×550px，视频卡片约452×550px，整组内容垂直居中。最终安装后再次验证相同指标；不会用普通浏览器尺寸替代真实最大化原生窗口验收。额外保留修改前2.2.2完整安装版及安装包于 `workspace/temp/ui-proportion-rollback-20261011/`。

## 最终安装验收（2.2.3）

- 实际安装位置：`D:/Apps/万象成文/wanxiang-chengwen.exe`，ProductVersion 和后端健康版本均为 2.2.3。主程序按精确 NSIS 标记差异归一化后与候选一致，后端逐字节一致。
- 安装包：`web/src-tauri/target/release/bundle/nsis/万象成文_2.2.3_x64-setup.exe`，112441768 字节，SHA-256 `2806d559acd3513c9435d736da4cdeacf26244e87b7b440d67109520c5cc7230`。
- 已安装主程序 SHA-256：`7dd2163bc754cd66efdc2c5f26eefd9c2823f5a398780ddf579a448c33d7e419`；后端：`4dd4b9d1af9f95b158d9b03f1a9d21192792da74ed74c5838eb1d6fb75292110`。
- 实际 WebView 的前端构建 ID：`5f8b6eecf19a-4566f5769010`；源码指纹、打包内容与真实窗口匹配。
- 无临时 CSS 注入的真实最大化安装窗口验证通过：工作区宽 980px，正文卡片约 510×550px，结果组高度 820px、垂直居中，无横向溢出。
- 四小时视频播放推进且解码有帧、无媒体错误；CUDA 实际推理、模型检测、保守校对及两版下载通过；九条原有 Capture 元数据逐字节保留。
- 正常启动已复核，GPU worker 在 NVIDIA 设备上，9222 调试端口已关闭。
- 最新视觉证据：`tests_runtime/ui-width-repair/installed-result.png` 和 `installed-home.png`；结构化报告：`installed-layout.json`、`installed-artifacts.json`、`installed-backend.json`、`installed-normal-launch.json`。
- 本轮未部署公开网站，亦未改变转写模型或宣称新的识别准确率提升。

# 本地升级验收 · 2026-10-11

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 专项记录；原实测日期保留 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

本文件记录 2.2.1 的已验收安装批次。后续用户要求恢复线上布局比例，2.2.3 的变更、交付与证据见 [布局修正](ui-width-repair-20261011.md)。默认 SenseVoice Small CUDA；原九条历史记录保留。公开网站和远端发布不属于本批次。

## 已落地

- SenseVoice Small CUDA：官方 CUDA 12/cuDNN 9 sherpa-onnx SDK，固定版本、revision、完整性校验。独立持久 worker 避免 CPU/GPU ONNX DLL 冲突；显式 CUDA 不退回 CPU。
- 后台预热、每批四段、流式解码约 29 秒缓冲，减少长音频内存峰值；回归确认采样完整及原有分块边界一致。
- 设置内选择模型/设备、查看最近实际设备并执行真实 CUDA 推理验证；切换设备保留两个模型路径。
- API 地址+密钥检测模型列表，再选择文字模型；支持手动模型名。密钥 DPAPI 加密，跨服务不隐式携带。
- 校对只提出候选，默认不选中；小范围、精确原文匹配、数量/数字/单位保护、重叠保护、原文哈希保护，确认后独立保存。原文/校对版分别导出 UTF-8 BOM 文件。
- 原品牌、黑白层级与另一窗口完成的结果页布局保留。设置/校对使用独立样式；键盘 Escape、焦点恢复、窄屏无横向溢出及按钮 ≥44px 已验收。
- 打包固定项目 Python 3.11 与 PyInstaller 6.22.3；新增后端源码指纹检测，打包期间源码变化会拒绝报告完成。桌面版本传给后端，选中 GPU 模型决定 readiness。

## 定量证据

报告：`workspace/benchmarks/sensevoice-same-model-cuda-20261011.json`。

| 同一 FP32 权重、3 分钟音频 | 三轮中位耗时 |
| --- | ---: |
| CPU，batch=1 | 12.346 秒 |
| CUDA，batch=1 | 1.135 秒 |
| CUDA，batch=4 | 0.869 秒 |

同批量、同权重 CUDA 为 10.873 倍；单批 CPU/CUDA 输出完全相同。四小时（音轨15426.247秒）实际流式解码+CUDA识别107.260秒，643段，无遗漏采样或异常。

原安装 CPU 权重是 WSYue 微调 int8，新 GPU 权重是通用 SenseVoice Small FP32。原CPU识别826.687秒与新107.260秒仅作为升级链路对照，不是同权重 CUDA 倍率，也不包含下载/提取时间。

准确率：现有5.592秒中文参考片段，新FP32输出在去标点后CER 0%；原int8此前为7.69%（同等数字写法）。没有四小时人工标注，也没有本机充分的英文/混说标准集，因此不宣称整体准确率或世界第一。公共模型数据与适用范围见 `asr-model-quantitative-audit-20261011.md`。

## 功能验证

- `scripts.verify --backend-only`：308 项运行，1 项线上样例测试跳过，其余通过；doctor、py_compile通过。
- TypeScript、Vite、Rust cargo check通过。
- 浏览器：设置四种视口、模型检测/保存/Escape/focus；嵌套校对/建议确认/分别下载/刷新恢复；另测首页、移动端深链、桌面结果16种组合。
- 补充完整界面回归：`python -m unittest tests.ui_smoke_suite`，25 项通过，覆盖处理中反馈、瞬时请求失败恢复、图片/Live Photo 查看器、手机触控、视频/音频结果、长图文与失败态。
- 打包程序及实际安装后端：真实CUDA worker推理及3分钟文件上传成功；模型列表、校对、原文不变、改数字拒绝、两版BOM下载、跨源请求拒绝。最终安装报告在 `tests_runtime/cuda-proofreading-verification/installed-backend.json`。
- 真实本机用户API地址/密钥未提供；功能HTTP测试使用仅本机回环地址的兼容服务，不发送用户内容至外部服务。

## 备份与边界

安装前备份：`workspace/temp/installed-rollback-20261011/app`（原程序完整文件树），`captures`（原历史元数据），`capture-hashes.json`（9份原记录哈希）。原音视频 artifacts 未修改。新偏好配置单独保存，不覆盖其他服务凭据。

回滚：先退出应用，确认没有处理任务，再将本次新程序目录备份为另一目录，将备份 `app` 复制回原 `D:/Apps/万象成文`；恢复备份偏好文件。原来没有偏好文件时，移走本次生成的 `local-preferences.json` 即恢复原默认设置。所有操作都要核实精确绝对路径。

本批次是本地交付；未提交当前混合脏工作区，未发布 GitHub Release、官网、HF或Worker。原项目运行环境及CPU模型保留。

## 实际安装身份与验收

- 安装位置：`D:/Apps/万象成文/wanxiang-chengwen.exe`，ProductVersion `2.2.1`；后端 `/health` 同为 `2.2.1`，真实设备 `cuda`、计算类型 `float32`、无 fallback。
- 安装包：`web/src-tauri/target/release/bundle/nsis/万象成文_2.2.1_x64-setup.exe`，112455134 字节（约107.24MiB）。GPU SDK/模型独立存储，未将其约1.14GB下载内容伪装成安装包体积。
- 安装包 SHA-256：`53aee9de75c1c9ffd5dfc0316dd98ba655f8c7558c015c5ea4284c37715e1ad3`。
- 已安装主 exe SHA-256：`b4f6ac2d862b477752bb9584a26d6374337dfb6a4da1f79d3d6c497eaf5e795e`。与候选 exe 在规范化精确 NSIS bundle marker 后逐字节一致。
- 已安装后端 SHA-256：`9984539a2321fdfc4fbd6002db19f8f9c4ca0c7c9487ecfbb1c9730eeae93b08`，与最终候选逐字节一致；后端源码指纹 `dca114276468cef7c61f9c315899b8ab43ef0e60ca977cde50f434de33bd285c` 再核对通过。
- 已安装 WebView 读取到的整个 `build-manifest.json` 与最终前端产物一致，build id `5f8b6eecf19a-dd43c4549611`。桌面后端仅托管 API，因此 `/health` 的前端 manifest 字段为空；前端身份由实际 WebView 内嵌资源单独确认，未误报成后端静态网页版本。
- 真实 WebView：打开设置→验证 CUDA→打开已有四小时结果→校对对话框→下载原文，控制台无 JS 错误；原视频实际播放、时间推进及解码帧均确认。下载文件 BOM 存在，去 BOM 后与 Capture 原文逐字相同；九条 Capture 元数据逐字节相同，桌面快捷方式目标未改变。
- 启动预热期间 `actual_device` 尚为空是未完成真实推理的状态，验收应在预热结束或点击验证后核对，不能将最初一次偏好响应当成最终设备状态。
- 证据：`installed-artifact-identity.json`、`installed-backend.json`、`installed-native-ui.json`、`installed-runtime-identity.json`、`installed-history.json`、`installed-normal-launch.json`，均位于 `tests_runtime/cuda-proofreading-verification/`。截图同目录保存。
- 验收后关闭临时 CDP 调试端口，正常启动已安装应用；正常实例的 worker 在 NVIDIA 进程列表中，9222 不再监听。

Windows测试清理：进程退出后日志句柄释放有短暂延迟，验收脚本和UI测试现在对自有临时目录做有上限的重试；两个浏览器交互测试及打包接口流程已重跑通过。

真实安装窗口补充：WebView2 将 Tauri 页面访问回环后端标为 cross-site，最初设置面板因此被依赖校验拒绝。已修复为仅在桌面运行目标、精确 Tauri origin 时放行该标记，仍由 middleware 校验桌面 token；新增回归验证恶意 origin 与普通网页仍被拒绝。主 exe 的 NSIS bundle marker 与未打包 exe 有精确三个字节变化，验收会规范化该已知标记后逐字节比较，其余差异不允许。

## 收口审查

按正确性、可读性、架构、安全和性能五个维度复核：默认模型家族、真实CUDA、禁止隐式CPU回退、原文哈希与独立文件、取消任务、批量时间线、错误净化、DPAPI与同源边界、进程隔离、有限缓冲及打包ABI均有对应检查。没有发现仍阻止本地安装的高优先级缺陷。实际外部模型效果和英文/混说准确率仍属于未验证范围，不以接口仿真或公开论文替代。

## 平台与设计补充复核

苹果官方材质/动效原则、成熟开源组件复用、六个平台本轮实链结果与小红书导航/抖音预算修复，详见 `product-quality-audit-20261011.md`。平台修复已包含在本文最终安装包与已安装后端哈希中。小红书一条在安装会话成功提取12图，另一条因网络风控未完成；不宣称全平台所有链接稳定成功。

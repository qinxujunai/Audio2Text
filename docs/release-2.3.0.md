# 万象成文 2.3.0 实施与发布记录

交付顺序：应用修复 → 实测并更新本地安装版 → 官网与 GitHub → 公开同一已验收文件。

## 已验证的源码功能

- 当前 DeepSeek 地址与密钥可读取模型，真实合成文本校对验证通过；没有证据表明原配置填写错误，也没有把过去的失败假称为已复现。
- 分区保存、草稿测试校对、模型列表与手动输入、结构化错误、独立运行设置。
- DeepSeek 请求采用官方 JSON 输出与关闭思考模式，输出预算有限；格式错误、空输出和截断明确区分。
- 分块检查点恢复、取消后丢弃迟到结果、配置或原文变化使旧建议失效、保存绑定任务编号。
- 原文保持不变，建议默认不勾选，两版独立下载。模型不能保证语义正确，仍需逐处人工确认。
- 首页设置位置与结果宽高比例未扩大；正文保持稳定底色，设置状态靠近字段，运行组件放入次级设置。
- 官网静态源码增加四种使用场景、流程、可交互的修正示例、隐私与下载说明，保留中文/英文与减少动态效果。

## 当前证据

本地证据目录：`tests_runtime/product-upgrade/`，不随源码公开用户资料。

| 项目 | 结果 / 证据 |
| --- | --- |
| 最新后端检查 | 348 tests，1 skipped；`final-feedback-backend.log`，包含 YouTube 登录限制的具体提示与脱敏回归 |
| 配置/协议/恢复回归 | 32 passed；包含密钥脱敏、旧任务拒绝与取消幂等 |
| 应用网页回归 | 25 passed；`web-ui-final.log` |
| 官网及本地设置 | 12 passed；`site-ui-final.log` |
| TypeScript / 前端构建 | 通过 |
| 真实 DeepSeek 协议 | 合成示例通过；`deepseek-actual-verification.json` |
| 打包后端 | CUDA 上传、检测、草稿测试、分区保存、过期拒绝与两版下载通过；`packaged-backend.json` |
| 原生安装窗口 | 2.3.0 实际安装通过；CUDA、播放、原文下载、九条历史哈希不变；`installed-native-ui.json`、`installed-layout.json` |
| 安装窗口配置测试 | DeepSeek 合成文本 593ms；按钮下一帧反馈 2.3ms；`installed-real-model-test.json`，单次耗时不是长期性能结论 |
| 2026-10-12 交互候选 | 新安装包已覆盖本地；DeepSeek 794ms、按钮反馈 1.7ms，CUDA/普通及最大化窗口/播放/原文/九条历史保留通过；见 `interaction-refinement-20261012.md` |
| 转写证据覆盖 | 6098 段独立音频、6.082 小时、三轮，九类分组逐条检查通过；派生音乐与重叠会议不增加独立时长，计分限制见 `ascend-accuracy-20261012.md` |
| 公共提取门槛 | 六个平台 60 链接三轮尚未完成，正式发布门槛未通过 |
| 干净 Windows | `b661b0e` 的已安装保存文件通过首次组件、CPU、播放及三类真实下载；[同文件 CI](https://github.com/qinxujunai/Audio2Text/actions/runs/38275967046)。后续文件仍须重新验收 |
| 正式 Release / 官网 | 当前仍为 2.1.0，本批次尚未公开 |

短示例原协议与修正协议都曾返回成功；一次耗时差异不代表准确率或长期性能结论。完整质量必须分别报告中文、英文、混说，CPU/CUDA 加速必须比较相同权重与参数。

## 本地升级入口

先构建并验证打包后端：

```powershell
.\scripts\build_desktop.ps1
.\.venv\Scripts\python.exe -m scripts.verify_desktop_upgrade --output tests_runtime\product-upgrade\packaged-backend.json
$setup = Get-Item 'web\src-tauri\target\release\bundle\nsis\万象成文_2.3.0_x64-setup.exe'
$digest = (Get-FileHash -LiteralPath $setup.FullName).Hash
.\scripts\upgrade_local.ps1 -Installer $setup.FullName -ExpectedSha256 $digest -VerificationReport tests_runtime\product-upgrade\packaged-backend.json -NativeDebug
.\.venv\Scripts\python.exe -m scripts.verify_native_ui --output tests_runtime\product-upgrade\installed-native-ui.json
```

该升级入口限制本机既有 `D:\Apps\万象成文`，不迁移软件，不修改快捷方式，不动驱动。公开用户直接使用 NSIS 安装包；此脚本不是其他电脑的通用安装器。原生验收后关闭调试启动，正常启动并确认调试端口关闭。

每次备份写入 `workspace/temp/product-rollback-<时间>/`，包含 `app/`、`metadata/`、历史哈希与升级记录。安装一致性失败自动恢复程序和已备份元数据；后续原生验收失败也必须用该备份恢复后重验，不能宣布已升级。

## 最终发布条件

冻结最终源码，重新构建、安装、验收；将该安装包复制为 `Wanxiang-Windows-x64-Setup.exe`，复制前后 SHA-256 必须相同。通过数据、基准、干净 Windows 和独立审查门槛后，发布该文件及校验值。CI 重建包只作候选，不替代验收包。官网从正式 Release 读取版本、下载 URL、大小与 SHA-256，重新下载核对。

官网演示已从实际安装的 2.3.0 WebView 采集，使用专用示例和预置结果，隐藏私人历史；12 秒视频明确标注不代表真实处理耗时。视频与封面位于 `docs/assets/` 并同步到 `site/assets/`，采集来源记录在本地 `public-demo-frames/provenance.json`。圆点图标沿用桌面原图，统一官网、分享图和演示。

2026-10-11 已安装候选来自 SHA-256 为 `96f015a6fb3ffa26243fdeffda9675096144e3589ba13d3a0c5162ef58b269ac` 的安装包，回滚目录为 `workspace/temp/product-rollback-20261011-220810/`。该哈希只标识当时安装来源，后续构建已覆盖原构建输出路径，不能将该路径下的新文件称为此候选。它不是最终公开冻结包；2026-10-12 交互与安装安全修复需重新构建、安装、验收，详见 `interaction-refinement-20261012.md`。

统一构建输出 `delivery-build-manifest.json`，绑定冻结源码、版本、主程序、后端与安装包哈希。升级脚本拒绝源码变化或资产不匹配，并执行保留的已验证安装包副本。NSIS 会保留旧运行组件和主程序到安装目录下的 `sidecar/wanxiang-api.rollback-<版本>-<时间>/`，路径记录于 `last-runtime-backup.txt`；该备份不自动删除。手动恢复应先退出程序，保留当前目录，再用备份中的 `wanxiang-chengwen.previous.exe` 恢复主程序、其余原运行组件恢复 runtime。使用本机升级入口时，优先按完整 `product-rollback` 备份恢复。

2026-10-12 后续独立审查与同包干净 Windows 验收见 `release-review-20261012.md`。完整 sidecar 文件清单和树摘要同时绑定构建、打包验收与安装；取消绑定 job_id；提取统计从原始记录推导并拒绝矛盾摘要。当时 347 项后端回归与 15 项官网/品牌回归已通过。正式发布仍等待最终文件复验及六平台三轮，不将旧候选的验收移用到新文件。

2026-10-13 的安装、进程启动/预热、长文滚动与取消基线见上述审查记录的新章节。后续仅补齐 YouTube 登录限制的原因/回退文案和正常退出/DPI 验收；原品牌、布局和转写模型保持不变。最新 348 项后端回归与新增浏览器用例通过，新增文案需完成下一份安装包与真实本机同步后才算交付。

固定 60 链接的完整三轮已完成，正式发布门槛未通过，见 [逐平台量化报告](public-platform-benchmark-20261013.md)。构建脚本另外修复了 PowerShell 5/7 排序差异，跨 shell 指纹回归通过；最终安装包仍须重新验收，不能继续使用失败构建输出。

## 2026-10-13 用户调整平台范围

用户明确移除 YouTube 支持及宣传，并停止 VPN/代理配置工作。源代码在新链接解析前拒绝该来源，adapter 不可复活该入口；首页、官网中英文、README 和默认实链测试仅保留其他五个平台。旧结果和媒体可继续查看、复制与下载。尚未交付的代理端口草稿已撤回，未写入用户设置、未进入安装包。

本次为新源码快照，必须重新构建与验收实际安装版，不能沿用 `931c68a` 的安装证据宣布交付。新的本地验收记录保存于 `tests_runtime/product-upgrade/youtube-retirement/`，安装回滚位置由统一升级入口生成。旧六平台三轮报告不删行、不补绿；公开发布仍要求当前五平台的 60 条独立链接、三轮及各平台每轮完整成功，其他门槛保持。小红书旧样本全部不可读取、其他平台仍有超时，故不能据移除 YouTube 宣称门槛通过。

该范围调整已通过 359 项后端检查（1 项按条件跳过）、前端类型检查与构建。独立审查关闭了短链重定向、默认会话页面及旧队列重试三处遗漏。发布门槛没有改为仅检查摘要或仅检查可成功平台。

2026-10-14 同步检查公开素材，发现旧录像与 README 首页截图仍显示 YouTube。已用实际安装的 `181f54a` WebView、专用公开示例及空历史重新录制 12 秒演示，替换官网/README 录像、封面与桌面截图；示例来源标签统一为抖音，未导出私人历史。此素材变更进入新冻结快照，最终安装包须再次验收，不沿用此前候选哈希。

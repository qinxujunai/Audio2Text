# 当前五平台三轮提取验证

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 专项记录；原实测日期保留 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

2026-10-21 完成 60 条独立链接、三轮共 180 次提取。源码冻结为 `f063cec36585369f660e2c10c63e9ea347b92cc9`，归档 SHA-256 为 `ee7462be29c504a6ac5887b810b5259f98d7083b37d5ee946ca6592765fdae6a`。每次上限 180 秒，全部失败保留。

样本保留旧批次五个仍支持平台的全部 50 条链接，加入用户提供的有效小红书分享和公开列表中最先取得的 9 条不同哔哩哔哩内容。没有根据成功结果筛选。样本集 SHA-256 为 `24ee68e5536e04a32cd92a2e971f46705f500d1c75f64a31a42b7782e707c7f2`。包含有效分享参数的原始地址只留在本地证据中。

| 平台 | 完整交付 / 尝试 | 未完成原因 |
| --- | ---: | --- |
| 哔哩哔哩 | 53 / 57 | 4 次下载阶段超时 |
| 小宇宙 | 27 / 30 | 3 次来源不可读取 |
| 抖音 | 24 / 30 | 6 次要求有效会话 |
| 小红书 | 3 / 33 | 旧链接 30 次来源不可读取；新分享三轮完整成功 |
| 微信公众号 | 30 / 30 | 本批次没有失败 |

完整率 137/180（76.11%），超时率 4/180（2.22%），全样本 P95 为 89.973 秒。它们只描述固定样本与本次访问条件，不能当作平台普遍成功率。样本范围与旧六平台批次不同，不用两批完整率差值宣传性能提升。

正式校验返回 `passed: false`：五个平台每轮均有完整样本，但存在超时。转写人工参考数据门槛单独通过，不能抵消提取失败。下载过程的临时文件持续增长说明该样本仍在传输，不能归因为 CUDA 推理慢；不同传输实现正在做同文件实验，尚不据此更换默认实现或宣传提速。

证据位于本地 `tests_runtime/product-upgrade/xhs-mobile-20261021/`：`current-five-platforms-report.json`、同名 checkpoint、`corpus-provenance.json`、`current-benchmark-validation.log`、`transfer-observations.jsonl`。基准中途主机会话终止后，在确认原进程已退出的情况下按冻结身份恢复 checkpoint，未重跑或删除已完成行。

复核命令：

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_benchmark_reports --extraction tests_runtime/product-upgrade/xhs-mobile-20261021/current-five-platforms-report.json --transcription tests_runtime/product-upgrade/sensevoice-all-slices-evidence.json
```

预期返回码 1。当前不把 2.3 候选包转为正式 Release；官网品牌及撤退宣传维护不能被描述成新安装包已正式发布。

## 2026-10-22 下载路径定向复核

未修改应用，对此前连续超时的 `current-bilibili-7` 使用同一来源、同一完整媒体和官方 aria2 四连接做三次有时限验证：158.281 秒完整下载，随后两次分别在 180.406、180.016 秒达到测试时限。完整文件 259809710 字节，SHA-256 为 `d4245872cdde9952c771fe32bc105461b50c9f088f4379553b0d952d2545ec6e`。只完成 1/3，不能宣称多连接已经解决发布超时，因此不增加运行组件或替换默认下载实现。

此前另一个完整文件的单轮筛查确有较快结果，但不能外推到这个失败样本。定向结果保存在本地 `timeout-targeted.json` 与 `timeout-targeted.log`；不混入冻结源码的正式三轮报告，也不提高测试时限或删除失败。aria2 来自[官方发布](https://github.com/aria2/aria2/releases/tag/release-1.37.0)，测试仅作为诊断，不代表新的产品依赖。

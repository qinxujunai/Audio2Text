# 当前五平台三轮提取验证

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

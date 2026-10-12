# 六平台公开链接三轮实测

2026-10-13 完成。固定 60 条不同内容身份的公开链接，每个平台 10 条，共三轮 180 次。提取源码冻结在 `b661b0e`；没有读取 YouTube 登录 Cookie，没有删除失败、换链接补绿或混合不同源码结果。后续 YouTube 提示文案和构建指纹修复不改变这次提取结果。

| 平台 | 完整交付 / 30 次 | 失败或不完整的原因 |
| --- | ---: | --- |
| 哔哩哔哩 | 28 | 2 次超时 |
| 抖音 | 21 | 8 次要求有效会话，1 次超时 |
| 微信公众号 | 30 | 本批次没有失败 |
| 小红书 | 0 | 30 次来源不可读取，页面返回 404 |
| 小宇宙 | 23 | 4 次超时，3 次来源不可读取 |
| YouTube | 0 | 26 次要求登录/验证身份，1 次网络失败；另 3 次仅有字幕，缺媒体 |

完整交付为 102/180（56.67%）；提取成功为 105/180（58.33%），其中三次字幕成功不能算完整视频交付。超时 7/180（3.89%），全样本 P95 为 151.716 秒，包括失败和超时。这些数字只描述此批固定样本、本机网络和匿名/既有项目会话条件，不是各平台普遍成功率。

小红书的来源不可读取不能仅凭 404 确定为内容下架，也可能涉及有效分享地址、访问条件或平台限制；本批没有足够证据区分。YouTube 没有登录，本机正常 HTTP/HTTPS 路由和 Node/EJS 已启用，但仍出现身份验证限制。不能用配置正常推断链接一定可读取。

本地证据：

- `workspace/benchmarks/public-links-20261012.jsonl`：固定样本，SHA-256 `68bb4fe2180f1fe15f771ddcbd1b948287c73644c39b0a06e531a8697a71fb5c`。
- `workspace/benchmarks/extraction-code-b661b0e.zip`：冻结源码，SHA-256 `6ba8d35fa88e8f80a62792852dde6b94775f348d0b135602ab0a7094785c93d5`。
- `workspace/benchmarks/public-links-frozen-b661b0e-20261012.json`：180 条逐次结果及来源身份。
- 同名 `.checkpoint.jsonl`：每次完成即持久化的记录，绑定样本及源码指纹。
- `tests_runtime/product-upgrade/final-benchmark-gate.json`：从原始结果重新计算的发布门槛。

复核命令：

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_benchmark_reports --extraction workspace/benchmarks/public-links-frozen-b661b0e-20261012.json --transcription tests_runtime/product-upgrade/sensevoice-all-slices-evidence.json
```

返回码 1，转写人工参考门槛通过，提取门槛未通过：两个平台没有完整成功，且存在超时。正式 Release 和官网更新必须停止；不能把通过的本地安装验收或转写准确率移作提取成功证据。后续修复需要保留本批结果，并独立记录新源码、新样本来源和完整三轮验证。

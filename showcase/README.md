# 万象成文演示片

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 项目维护文档 · [最新状态](../docs/project-status.md) · [文档索引](../docs/README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

当前官网的 12 秒无声演示片从实际安装的 2.3.0 WebView 采集。资料与结果为专用 fixture，隐藏用户历史，不进行真实平台抓取或耗时展示。采集帧编码为 1920×1080、H.264、30fps。

下方 Remotion 命令仍保留为代码生成演示工具，会覆盖当前演示片；只能在审查新素材与版本一致性后执行。

```powershell
cd showcase
npm ci
npm run render
```

输出包括演示片、poster、社交预览图，并自动同步到 `site/assets/`。

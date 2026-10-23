# 万象成文演示片

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 项目维护文档 · [最新状态](../docs/project-status.md) · [文档索引](../docs/README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

当前官网与 README 的 12 秒无声演示片是一次真实处理的录屏（2026-10-23）：在本仓库网页版界面上传一段 52 秒的中文视频，SenseVoice（int8，CPU）5.3 秒转写完；画面里的文字、视频、按钮状态都来自那次运行，只把等待部分加速，编码为 1920×1080、H.264、30fps。素材：中国新闻社《王忠民：人工智能算力的尽头是电力，电力尽头是绿电》，Wikimedia Commons，CC BY 3.0（https://commons.wikimedia.org/wiki/File:王忠民：人工智能算力的尽头是电力，电力尽头是绿电.webm）；只录屏，未改动原片。云端录屏用的 Chromium 不能解码 H.264，所以播放器里放的是同一段素材的 VP9 版本，画面一致。

上一版（2026-10-14）用预置示例和固定结果，他看了说右边的视频「很虚假」，所以换成真实处理。

下方 Remotion 命令是旧的代码生成演示工具，会用预置画面覆盖当前的真实录屏，不要再用它生成官网演示。

```powershell
cd showcase
npm ci
npm run render
```

输出包括演示片、poster、社交预览图，并自动同步到 `site/assets/`。

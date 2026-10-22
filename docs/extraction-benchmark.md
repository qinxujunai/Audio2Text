# Extraction Benchmark

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 项目维护文档 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

Public-link extraction changes are promoted by measured success and latency, not by framework popularity.

The benchmark reads a private JSONL manifest so public test URLs do not need to be committed. Each line defines one stable sample and its minimum completeness contract:

```jsonl
{"id":"xhs-01","url":"https://example.com/note","platform":"xiaohongshu","minimum_text_chars":80,"minimum_images":3,"require_media":false}
```

Run the current implementation three times:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_extraction `
  --manifest workspace\benchmarks\extraction-public.jsonl `
  --repeat 3 `
  --json-out workspace\benchmarks\extraction-baseline.json
```

Run a candidate with the identical manifest and compare it with the baseline:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_extraction `
  --manifest workspace\benchmarks\extraction-public.jsonl `
  --repeat 3 `
  --baseline-report workspace\benchmarks\extraction-baseline.json `
  --json-out workspace\benchmarks\extraction-candidate.json
```

The release corpus must contain at least 60 unique links and every candidate must complete three runs. A candidate may replace the default path only when the generated report says `eligible_to_replace_default: true`. That requires either a success-rate gain of at least five percentage points, or no success-rate regression and a p95 latency reduction of at least 20%.

The report also records content-completeness, media readiness, whether downstream transcription is required, fallback rate, text and image yield, attempt count, strategy, browser-runtime state, and sanitized error codes. Keep manifests and reports under `workspace/benchmarks/`; links may expire and should be refreshed before a release decision.

Final evidence validation reads raw rows, not only the summary. Each of sixty
distinct canonical content identities must have rounds 1, 2 and 3, with all five
currently supported platforms covered (Bilibili, Xiaoyuzhou, Douyin, Xiaohongshu,
and WeChat articles). Each platform needs a complete success in every round,
and benchmark timeouts must be zero. Historical six-platform reports retain
all YouTube observations and failures; they are not rewritten as current evidence.
URL aliases and tracking parameters do not create new
content. Expired or challenged links remain failed observations; unidentified
short links require resolved canonical identities before they can count.

Each sample is isolated behind a killable process boundary. The default per-run
timeout is 180 seconds, and a stalled public provider is recorded as
`benchmark_timeout` before the next sample starts. Use
`--sample-timeout-seconds` to set a lower CI budget; do not treat a timeout as
an application crash without checking the provider trace and the corresponding
live-link result.

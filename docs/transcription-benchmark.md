# Transcription Benchmark

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 项目维护文档 · [最新状态](project-status.md) · [文档索引](README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

Audio2Text should choose local ASR models by measured behavior, not by model size alone.

Current Windows desktop configuration uses SenseVoice Small. The explicit CUDA
route uses FP32 weights and the isolated official sherpa-onnx CUDA SDK; the CPU
compatibility route uses the existing fine-tuned int8 weights. These are different
weights and their timings must not be described as pure CUDA acceleration.
Whisper remains an optional model. Explicit CUDA initialization failure is an
error, never a silent CPU fallback. Runtime device reporting is authoritative.
The 72-sample / 4-hour / 3-run benchmark with human references still applies;
Chinese, English and code-switching quality must be reported separately.

Historical smoke: an additional 989-second Bilibili course sample measured SenseVoice at 48.21 seconds
(RTF 0.049, 5,604 characters) and Faster-Whisper medium on an RTX 3060 at 96.56
seconds (RTF 0.098, 5,739 characters). This compares different models and weights,
not GPU acceleration or proven accuracy. It does not define the current routing policy.

Use `scripts.benchmark_transcription` to compare Faster-Whisper model directories or Hugging Face model IDs with the same audio samples.

The benchmark prints model-load and sample-level progress to stderr and truncates
long transcript previews in the terminal. This keeps long local runs observable;
the JSON report still contains the complete transcript and metrics.

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_transcription `
  --manifest workspace\benchmarks\local-samples.jsonl `
  --model medium=workspace\runtime\models\faster-whisper\medium `
  --device cuda `
  --compute-type float16 `
  --beam-size 1 `
  --repeat 3 `
  --json-out workspace\benchmarks\medium-beam1.json
```

Manifest format:

```jsonl
{
  "id": "zh_business_hours",
  "audio": "E:/path/to/zh.wav",
  "reference": "开放时间早上9点至下午5点",
  "reference_kind": "human",
  "reference_source": "人工标注来源或固定版本的数据集与样本编号",
  "terms": [
    "9点",
    "5点"
  ]
}
```

Selection rule:

- The release manifest must contain at least 72 unique clips and four hours across
  Mandarin, dialect/Cantonese, mixed language, English, multi-speaker, noise, music,
  proper nouns, and long-form slices. Every candidate runs three times.
- Reports include CER, WER, mixed error rate, term recall, silence hallucinations,
  first-token time, p50/p95 RTF, failure/OOM rate, model size, and observed peak VRAM.
- A candidate advances when mixed error improves by at least 8%, or p95 RTF improves
  by at least 25% while mixed error worsens by no more than 0.5 percentage points.
- No critical slice may worsen by more than one absolute percentage point. The run
  must have no failures, use no more than 5.2 GB VRAM, and ship no more than 2 GB of
  model files.
- If references are absent, the benchmark is only a speed and stability smoke test.
- Final validation checks every raw sample, three distinct rounds per sample,
  SHA-256 identity, and an explicit human reference with provenance. Summary
  flags alone cannot pass. Derived music/noise variants must not inflate
  independent corpus hours; report their origin and augmentation separately.
- Keep large model experiments under `workspace/runtime/models/faster-whisper/` and do not replace the current model until the benchmark report proves a better tradeoff.

The historical Faster-Whisper comparison profile on the RTX 3060 Laptop GPU 6GB is:

- model: `workspace/runtime/models/faster-whisper/medium`
- device: `cuda`
- compute type: `float16`
- beam size: `1`
- VAD: enabled

This profile is a benchmark candidate, not the default. Larger candidates such as
CTranslate2 `large-v3-turbo`, Qwen3-ASR 0.6B, and Fun-ASR-Nano GGUF should be
downloaded only in an isolated benchmark
workspace and selected only after they satisfy the repository quality and speed gate;
they are not part of the standard Windows runtime.

Qwen3-ASR 1.7B, GLM-ASR, and FireRedASR2 are hardware-preflight candidates only on
the 6 GB Windows target. Crawl/model popularity is not evidence for promotion, and
no candidate is enabled until the generated JSON contains
`eligible_to_replace_default: true`.

## Local media smoke (2026-09-26)

Five locally available audio files were run three times through the current
Faster-Whisper medium route and SenseVoice int8. The set totals 0.551 hours and is
only a speed/stability smoke because it does not contain the required 72 unique
annotated clips. Both engines completed all 15 runs for their respective routes.

- Faster-Whisper medium: p50 RTF 0.0715, p95 RTF 0.0935, 0 failures.
- SenseVoice int8: p50 RTF 0.0303, p95 RTF 0.0309, 0 failures.
- The one referenced Mandarin clip gave Faster-Whisper CER 0.0000 and full term
  recall; SenseVoice gave CER 0.2308 and term recall 0.0000.
- Decision: SenseVoice is not eligible to replace the default. The report is
  stored at `workspace/benchmarks/current-media-smoke-20260926.json`.

## Latest local smoke (2026-09-19)

One 5.592-second Mandarin clip was run three times on the RTX 3060 Laptop target.
Faster-Whisper medium produced p50 RTF 0.1098 with CER 0.0000 and full term recall.
SenseVoice int8 produced p50 RTF 0.0395, but CER 0.2308 and zero recall for the two
reference terms. The generated decision therefore marks SenseVoice
`eligible_to_replace_default: false`: it is much faster on this clip, but the quality
and dataset gates both fail. This is a smoke result, not a model ranking; the report is
stored locally as `workspace/benchmarks/final-local-smoke-20260919.json`.

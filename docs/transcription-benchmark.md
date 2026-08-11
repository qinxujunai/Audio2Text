# Transcription Benchmark

Audio2Text should choose local ASR models by measured behavior, not by model size alone.

The Windows default is SenseVoice int8 on CPU because it runs on machines without
CUDA and avoids shipping multiple model families. The current one-sample hardware
probe measured approximately RTF 0.035 on CPU, versus Faster-Whisper medium at
approximately RTF 0.153 on RTX 3060 and 2.01 on CPU. This is a speed and packaging
decision, not a final accuracy verdict; the 60-sample / 3-hour benchmark gate still
applies before replacing the precision profile.

An additional 989-second Bilibili course sample measured SenseVoice at 48.21 seconds
(RTF 0.049, 5,604 characters) and Faster-Whisper medium on an RTX 3060 at 96.56
seconds (RTF 0.098, 5,739 characters). The desktop `auto` policy therefore keeps
SenseVoice for Chinese-first platforms. It selects GPU Faster-Whisper only when a
validated model is already available and the platform or language benefits from the
precision profile; initialization failure falls back to SenseVoice.

Use `scripts.benchmark_transcription` to compare Faster-Whisper model directories or Hugging Face model IDs with the same audio samples.

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_transcription `
  --manifest workspace\benchmarks\local-samples.jsonl `
  --model medium=workspace\runtime\models\faster-whisper\medium `
  --device cuda `
  --compute-type float16 `
  --beam-size 1 `
  --json-out workspace\benchmarks\medium-beam1.json
```

Manifest format:

```jsonl
{
  "id": "zh_business_hours",
  "audio": "E:/path/to/zh.wav",
  "reference": "开放时间早上9点至下午5点",
  "terms": [
    "9点",
    "5点"
  ]
}
```

Selection rule:

- Prefer the lowest character error rate when references exist.
- If accuracy ties, prefer the lower real-time factor.
- If references are absent, the benchmark is only a speed and stability smoke test.
- Keep large model experiments under `workspace/runtime/models/faster-whisper/` and do not replace the current model until the benchmark report proves a better tradeoff.

On the RTX 3060 Laptop GPU 6GB setup, the current production baseline is:

- model: `workspace/runtime/models/faster-whisper/medium`
- device: `cuda`
- compute type: `float16`
- beam size: `1`
- VAD: enabled

This GPU baseline remains the precision profile. Larger candidates such as
CTranslate2 `large-v3-turbo` or Qwen3-ASR should be downloaded only in a benchmark
workspace and selected only after they satisfy the repository quality and speed gate;
they are not part of the standard Windows runtime.

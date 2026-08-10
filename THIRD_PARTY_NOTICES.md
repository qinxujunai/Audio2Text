# Third-Party Notices

This file records the direct dependencies shipped by the project. Release
automation must generate an SBOM and preserve each dependency's complete
license text in the installer artifact.

| Component | Purpose | License |
| --- | --- | --- |
| FastAPI / Uvicorn | Local API service | MIT / BSD-3-Clause |
| Pydantic | API validation | MIT |
| HTTPX / Requests | HTTP clients | BSD-3-Clause / Apache-2.0 |
| yt-dlp | Public media extraction | Unlicense |
| Playwright | Controlled browser fallback | Apache-2.0 |
| FFmpeg 9.0 Essentials (Gyan.dev Windows build) | Media conversion | GPL-3.0-only; distributed as a separate runtime package |
| Faster-Whisper / CTranslate2 | Local transcription | MIT |
| ONNX Runtime | Optional native model runtime | MIT |
| PyAV | Media inspection | BSD-3-Clause |
| React / React DOM | Web interface | MIT |
| Motion | Interface motion | MIT |
| Lucide | Interface icons | ISC |
| Tauri | Windows desktop shell | Apache-2.0 / MIT |

## Model Weights

Model weights are not licensed by this repository's Apache-2.0 license.
The standard runtime package contains only the int8 SenseVoice model converted
by sherpa-onnx from `ASLP-lab/WSYue-ASR` (Apache-2.0):
`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09`. Faster-Whisper and
Qwen3-ASR remain optional benchmark candidates and are not shipped.

## Runtime Sources

- SenseVoice int8 model: https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
- SenseVoice conversion documentation: https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
- SenseVoice fine-tune source and license: https://huggingface.co/ASLP-lab/WSYue-ASR
- FFmpeg 9.0 Essentials binary and checksums: https://www.gyan.dev/ffmpeg/builds/
- FFmpeg corresponding source: https://github.com/FFmpeg/FFmpeg/tree/n9.0

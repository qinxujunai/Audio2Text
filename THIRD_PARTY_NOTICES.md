# Third-Party Notices

<!-- project-doc-status:start -->
> 文档维护同步：**2026-10-22** · 项目维护文档 · [最新状态](docs/project-status.md) · [文档索引](docs/README.md)
> 本页属于 2.3 候选文档快照；默认分支代码与正式 2.1 安装包不因此升级。维护日期不代表重新执行历史测试。
<!-- project-doc-status:end -->

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
| sherpa-onnx | SenseVoice CPU and isolated CUDA inference | Apache-2.0 |
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

The optional local CUDA profile additionally uses the original
`FunAudioLLM/SenseVoiceSmall` (Apache-2.0), exported by the sherpa-onnx maintainer
as `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`.
It is a different weight variant from the WSYue int8 compatibility profile.
The model and official GPU wheel are separately downloaded with pinned revisions
and hashes by `scripts/prepare_sensevoice_cuda.py`; GPU drivers/CUDA/cuDNN are
not redistributed in this installer. Component provenance lives beside the SDK.

## Runtime Sources

- SenseVoice int8 model: https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
- SenseVoice conversion documentation: https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
- SenseVoice fine-tune source and license: https://huggingface.co/ASLP-lab/WSYue-ASR
- FFmpeg 9.0 Essentials binary and checksums: https://www.gyan.dev/ffmpeg/builds/
- FFmpeg corresponding source: https://github.com/FFmpeg/FFmpeg/tree/n9.0




# Public installation test speech

`tests/fixtures/asr-smoke.wav` is an ASCEND public speech sample under CC-BY-SA-4.0. Its fixed dataset revision, sample identifier, human reference and publication attribution are recorded in `tests/fixtures/asr-smoke-license.json`. It is not user audio and is not used to advertise an accuracy percentage.

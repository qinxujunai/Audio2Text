from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import av
import httpx

from app.settings import (
    DEFAULT_TIMEOUT_SECONDS,
    DEPLOYMENT_MODE,
    OPENAI_COMPATIBLE_API_KEY,
    OPENAI_COMPATIBLE_BASE_URL,
    OPENAI_COMPATIBLE_MODEL,
    DEVICE,
    MODEL_PATH,
    SENSEVOICE_MODEL_DIR,
    TRANSCRIPTION_AVAILABLE,
    TRANSCRIPTION_PROVIDER,
    WORKER_TRANSCRIBE_URL,
    WORKER_TRANSCRIBE_SHARED_SECRET,
)
from scripts.run_transcribe import transcribe_file


@dataclass(slots=True)
class TranscriptionResult:
    provider: str
    transcript_text: str
    timeline_text: str
    language: str | None = None
    segment_count: int = 0


TranscriptionProgressCallback = Callable[[float, str], None]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_audio_duration(path: Path) -> float:
    with av.open(str(path)) as container:
        if container.duration is not None:
            return max(float(container.duration) / 1_000_000.0, 0.001)
        durations = [
            float(stream.duration * stream.time_base)
            for stream in container.streams
            if stream.type == "audio" and stream.duration is not None and stream.time_base is not None
        ]
    if not durations:
        raise RuntimeError("无法确认音频时长，云端转写已停止。")
    return max(max(durations), 0.001)


def _build_worker_auth_headers(path: Path, *, secret: str, duration_seconds: float) -> dict[str, str]:
    if not secret:
        raise RuntimeError("云端转写服务尚未完成安全配置。")
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    duration = f"{duration_seconds:.3f}"
    body_hash = _sha256_file(path)
    service = "wanxiang-backend"
    payload = "\n".join([timestamp, nonce, duration, body_hash, service])
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(path.stat().st_size),
        "X-Praxis-Timestamp": timestamp,
        "X-Praxis-Nonce": nonce,
        "X-Praxis-Audio-Duration": duration,
        "X-Praxis-Content-SHA256": body_hash,
        "X-Praxis-Service": service,
        "X-Praxis-Signature": signature,
    }


class TranscriptionProvider(Protocol):
    name: str

    def transcribe(
        self,
        media_file_path: str,
        *,
        capture_id: str,
        output_folder: str | Path,
        output_stem: str = "raw_transcript",
        source_url: str | None = None,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> TranscriptionResult:
        ...


class LocalFasterWhisperProvider:
    name = "local_faster_whisper"

    def transcribe(
        self,
        media_file_path: str,
        *,
        capture_id: str,
        output_folder: str | Path,
        output_stem: str = "raw_transcript",
        source_url: str | None = None,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> TranscriptionResult:
        payload = transcribe_file(
            media_file_path,
            url=source_url,
            output_folder=output_folder,
            output_stem=output_stem,
            progress_callback=progress_callback,
        ).get("result", {})
        transcript_path = Path(payload.get("txt_file") or "")
        timeline_path = Path(payload.get("timestamp_file") or "")
        transcript_text = transcript_path.read_text(encoding="utf-8", errors="ignore") if transcript_path.exists() else ""
        timeline_text = timeline_path.read_text(encoding="utf-8", errors="ignore") if timeline_path.exists() else ""
        return TranscriptionResult(
            provider=self.name,
            transcript_text=transcript_text.strip(),
            timeline_text=timeline_text.strip(),
            language=payload.get("language"),
            segment_count=int(payload.get("segment_count") or 0),
        )


class OpenAICompatibleTranscriptionProvider:
    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def transcribe(
        self,
        media_file_path: str,
        *,
        capture_id: str,
        output_folder: str | Path,
        output_stem: str = "raw_transcript",
        source_url: str | None = None,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> TranscriptionResult:
        if not self.base_url or not self.api_key:
            raise RuntimeError("OpenAI-compatible transcription provider is not configured.")

        target = f"{self.base_url}/audio/transcriptions"
        file_path = Path(media_file_path)
        if not file_path.exists():
            raise FileNotFoundError(media_file_path)

        headers = {"Authorization": f"Bearer {self.api_key}"}
        if progress_callback is not None:
            progress_callback(0.1, "正在提交媒体文件进行转写。")
        with file_path.open("rb") as handle, httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS * 4, trust_env=False) as client:
            response = client.post(
                target,
                headers=headers,
                data={"model": self.model},
                files={"file": (file_path.name, handle, "application/octet-stream")},
            )
            response.raise_for_status()
            payload = response.json()
        if progress_callback is not None:
            progress_callback(1.0, "转写结果已返回，正在整理文本。")

        transcript_text = str(payload.get("text") or "").strip()
        return TranscriptionResult(
            provider=self.name,
            transcript_text=transcript_text,
            timeline_text="",
            language=payload.get("language"),
            segment_count=0,
        )


class CloudflareWorkersAIWhisperProvider:
    name = "cloudflare_workers_ai"

    def __init__(self, *, transcribe_url: str, shared_secret: str) -> None:
        self.transcribe_url = transcribe_url
        self.shared_secret = shared_secret

    def transcribe(
        self,
        media_file_path: str,
        *,
        capture_id: str,
        output_folder: str | Path,
        output_stem: str = "raw_transcript",
        source_url: str | None = None,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> TranscriptionResult:
        file_path = Path(media_file_path)
        if not file_path.exists():
            raise FileNotFoundError(media_file_path)

        if progress_callback is not None:
            progress_callback(0.1, "正在通过云端免费 Whisper 进行转写。")

        duration_seconds = _probe_audio_duration(file_path)
        headers = _build_worker_auth_headers(
            file_path,
            secret=self.shared_secret,
            duration_seconds=duration_seconds,
        )
        with file_path.open("rb") as handle:
            with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS * 10, trust_env=False) as client:
                response = client.post(
                    self.transcribe_url,
                    headers=headers,
                    content=handle,
                )
                response.raise_for_status()
                payload = response.json()

        if "error" in payload:
            raise RuntimeError(f"Cloudflare Workers AI transcription failed: {payload['error']}")

        if progress_callback is not None:
            progress_callback(1.0, "转写结果已返回，正在整理文本。")

        transcript_text = str(payload.get("text") or "").strip()
        return TranscriptionResult(
            provider=self.name,
            transcript_text=transcript_text,
            timeline_text="",
            language=None,
            segment_count=0,
        )


class LocalSenseVoiceProvider:
    name = "local_sensevoice"

    def __init__(self, model_dir: str | Path | None = None, *, num_threads: int | None = None) -> None:
        self.model_dir = Path(model_dir or SENSEVOICE_MODEL_DIR)
        self.num_threads = max(1, num_threads or 2)
        self._recognizer_instance = None

    def _recognizer(self):
        if self._recognizer_instance is not None:
            return self._recognizer_instance
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError("轻量转写组件尚未安装完整。") from exc
        model_path = self.model_dir / "model.int8.onnx"
        tokens_path = self.model_dir / "tokens.txt"
        if not model_path.is_file() or not tokens_path.is_file():
            raise RuntimeError("轻量转写模型尚未准备完成。")
        self._recognizer_instance = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_path),
            tokens=str(tokens_path),
            num_threads=self.num_threads,
            provider="cpu",
            language="auto",
            use_itn=True,
        )
        return self._recognizer_instance

    @staticmethod
    def _decode_audio(path: Path):
        import numpy as np

        chunks = []
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise RuntimeError("媒体文件中没有可识别的音轨。")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    chunks.append(converted.to_ndarray().reshape(-1))
            for converted in resampler.resample(None):
                chunks.append(converted.to_ndarray().reshape(-1))
        if not chunks:
            raise RuntimeError("媒体文件中没有可识别的音频内容。")
        return np.concatenate(chunks).astype("float32", copy=False)

    def transcribe(
        self,
        media_file_path: str,
        *,
        capture_id: str,
        output_folder: str | Path,
        output_stem: str = "raw_transcript",
        source_url: str | None = None,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> TranscriptionResult:
        path = Path(media_file_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if progress_callback is not None:
            progress_callback(0.1, "正在使用轻量引擎识别音频。")
        samples = self._decode_audio(path)
        recognizer = self._recognizer()
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        recognizer.decode_stream(stream)
        transcript = str(stream.result.text or "").strip()
        if progress_callback is not None:
            progress_callback(1.0, "音频识别完成，正在整理文字。")
        return TranscriptionResult(
            provider=self.name,
            transcript_text=transcript,
            timeline_text="",
            language=None,
            segment_count=1 if transcript else 0,
        )


class UnavailableTranscriptionProvider:
    name = "unavailable"

    def transcribe(
        self,
        media_file_path: str,
        *,
        capture_id: str,
        output_folder: str | Path,
        output_stem: str = "raw_transcript",
        source_url: str | None = None,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> TranscriptionResult:
        raise RuntimeError("当前云端预览未配置转写服务。请配置 OpenAI-compatible 转写服务后再处理音视频。")


def get_transcription_provider() -> TranscriptionProvider:
    if TRANSCRIPTION_PROVIDER == "openai_compatible" and OPENAI_COMPATIBLE_BASE_URL and OPENAI_COMPATIBLE_API_KEY and OPENAI_COMPATIBLE_MODEL:
        return OpenAICompatibleTranscriptionProvider(
            base_url=OPENAI_COMPATIBLE_BASE_URL,
            api_key=OPENAI_COMPATIBLE_API_KEY,
            model=OPENAI_COMPATIBLE_MODEL,
        )
    if DEPLOYMENT_MODE == "cloud_preview" and WORKER_TRANSCRIBE_SHARED_SECRET:
        return CloudflareWorkersAIWhisperProvider(
            transcribe_url=WORKER_TRANSCRIBE_URL,
            shared_secret=WORKER_TRANSCRIBE_SHARED_SECRET,
        )
    sensevoice_ready = (SENSEVOICE_MODEL_DIR / "model.int8.onnx").is_file() and (
        SENSEVOICE_MODEL_DIR / "tokens.txt"
    ).is_file()
    faster_whisper_ready = MODEL_PATH.is_dir()
    if TRANSCRIPTION_PROVIDER == "local_sensevoice" and sensevoice_ready:
        return LocalSenseVoiceProvider()
    if TRANSCRIPTION_PROVIDER == "auto":
        if DEVICE == "cuda" and faster_whisper_ready:
            return LocalFasterWhisperProvider()
        if sensevoice_ready:
            return LocalSenseVoiceProvider()
        if faster_whisper_ready:
            return LocalFasterWhisperProvider()
    if TRANSCRIPTION_AVAILABLE and TRANSCRIPTION_PROVIDER == "openai_compatible":
        return UnavailableTranscriptionProvider()
    if TRANSCRIPTION_AVAILABLE:
        return LocalFasterWhisperProvider()
    return UnavailableTranscriptionProvider()

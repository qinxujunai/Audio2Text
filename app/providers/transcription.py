from __future__ import annotations

import hashlib
import hmac
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
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


VAD_MODEL_FILENAME = "silero_vad.onnx"


def _safe_duration(path: Path) -> float:
    try:
        return _probe_audio_duration(path)
    except Exception:
        return 0.0


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _recognition_progress(done_seconds: float, duration_seconds: float) -> tuple[float, str]:
    """Progress in listening time, which is what a person can relate to."""
    if duration_seconds > 0:
        done = min(done_seconds, duration_seconds)
        ratio = min(0.99, 0.02 + 0.97 * done / duration_seconds)
        return ratio, f"已识别 {_clock(done)} / {_clock(duration_seconds)}"
    return 0.5, f"已识别 {_clock(done_seconds)}"


def _iter_vad_chunks(path: Path, vad_model: Path, sample_rate: int, max_chunk_seconds: float):
    """Stream-decode ``path`` and yield ``(start_sample, samples)`` split at pauses."""
    import numpy as np
    import sherpa_onnx

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(vad_model)
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = 0.3
    config.silero_vad.min_speech_duration = 0.25
    config.silero_vad.max_speech_duration = 20
    config.sample_rate = sample_rate
    config.num_threads = 1
    window = config.silero_vad.window_size
    detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=max_chunk_seconds * 3)
    gap = np.zeros(int(sample_rate * 0.1), dtype="float32")
    limit = int(max_chunk_seconds * sample_rate)
    pending: list = []
    pending_start = 0
    pending_end = 0

    def flush():
        nonlocal pending
        if not pending:
            return None
        pieces = []
        for index, segment in enumerate(pending):
            if index:
                pieces.append(gap)
            pieces.append(segment)
        item = (pending_start, np.concatenate(pieces).astype("float32", copy=False))
        pending = []
        return item

    def drain():
        nonlocal pending_start, pending_end
        while not detector.empty():
            segment = detector.front
            start = int(segment.start)
            samples = np.asarray(segment.samples, dtype="float32")
            detector.pop()
            if pending and (start + len(samples) - pending_start) > limit:
                item = flush()
                if item is not None:
                    yield item
            if not pending:
                pending_start = start
            pending.append(samples)
            pending_end = start + len(samples)

    remainder = np.zeros(0, dtype="float32")
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise RuntimeError("媒体文件中没有可识别的音轨。")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)

        def frames():
            for frame in container.decode(audio=0):
                yield from resampler.resample(frame)
            yield from resampler.resample(None)

        for converted in frames():
            data = np.concatenate([remainder, converted.to_ndarray().reshape(-1).astype("float32", copy=False)])
            usable = len(data) - len(data) % window
            for offset in range(0, usable, window):
                detector.accept_waveform(data[offset:offset + window])
            remainder = data[usable:]
            yield from drain()
    if len(remainder):
        detector.accept_waveform(np.concatenate([remainder, np.zeros(window - len(remainder), dtype="float32")]))
    detector.flush()
    yield from drain()
    item = flush()
    if item is not None:
        yield item


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


@lru_cache(maxsize=1)
def _nvidia_gpu_memory_mb() -> int:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return 0
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if completed.returncode != 0:
        return 0
    memory_values = []
    for line in completed.stdout.splitlines():
        try:
            memory_values.append(int(line.strip()))
        except ValueError:
            continue
    return max(memory_values, default=0)


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
        _transcription_runtime.update(active_provider=self.name, fallback_active=False,
                                      actual_device=payload.get("device"), compute_type=payload.get("compute_type"))
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
    sample_rate = 16000
    target_chunk_seconds = 24
    max_chunk_seconds = 28

    def __init__(self, model_dir: str | Path | None = None, *, num_threads: int | None = None) -> None:
        self.model_dir = Path(model_dir or SENSEVOICE_MODEL_DIR)
        import os
        self.num_threads = max(1, num_threads or max(2, min(4, (os.cpu_count() or 4) // 2)))
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

    @classmethod
    def _split_audio(cls, samples):
        target_samples = cls.target_chunk_seconds * cls.sample_rate
        max_samples = cls.max_chunk_seconds * cls.sample_rate
        if len(samples) <= max_samples:
            return [samples]

        import numpy as np

        chunks = []
        start = 0
        total = len(samples)
        search_radius = 3 * cls.sample_rate
        energy_window = max(1, cls.sample_rate // 3)
        search_step = max(1, cls.sample_rate // 10)
        minimum_chunk = 8 * cls.sample_rate

        while total - start > max_samples:
            target = start + target_samples
            search_start = max(start + minimum_chunk, target - search_radius)
            search_end = min(start + max_samples, target + search_radius)
            candidates = range(search_start, search_end + 1, search_step)

            def boundary_energy(index: int) -> float:
                half_window = energy_window // 2
                window = samples[max(start, index - half_window) : min(total, index + half_window)]
                return float(np.mean(np.abs(window))) if len(window) else float("inf")

            end = min(candidates, key=boundary_energy, default=min(target, total))
            chunks.append(samples[start:end])
            start = end

        if start < total:
            chunks.append(samples[start:total])
        return chunks

    def _vad_model_path(self) -> Path | None:
        for folder in (self.model_dir, SENSEVOICE_MODEL_DIR):
            candidate = Path(folder) / VAD_MODEL_FILENAME
            if candidate.is_file():
                return candidate
        return None

    def _iter_timed_chunks(self, path: Path):
        """Yield ``(start_sample, samples)`` cut at pauses in speech.

        Silero VAD (shipped next to the model) finds where people stop talking;
        neighbouring speech is merged into pieces of at most ``max_chunk_seconds``
        so a sentence is not cut in half. Without the VAD model, or when it finds
        no speech at all, fall back to the energy-based fixed-length pieces.
        """
        vad_model = self._vad_model_path()
        if vad_model is not None:
            produced = False
            try:
                for item in _iter_vad_chunks(path, vad_model, self.sample_rate, self.max_chunk_seconds):
                    produced = True
                    yield item
            except ImportError:
                produced = False
            if produced:
                return
        offset = 0
        for chunk in self._iter_audio_chunks(path):
            yield offset, chunk
            offset += len(chunk)

    @classmethod
    def _iter_audio_chunks(cls, path: Path):
        """Decode long media with at most ~29 seconds of resident PCM."""
        import numpy as np
        frames, buffered = [], 0
        threshold = (cls.max_chunk_seconds + 0.5) * cls.sample_rate
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise RuntimeError("媒体文件中没有可识别的音轨。")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=cls.sample_rate)
            def consume(converted):
                nonlocal frames, buffered
                for frame in converted:
                    samples = frame.to_ndarray().reshape(-1)
                    frames.append(samples)
                    buffered += len(samples)
                    if buffered >= threshold:
                        combined = np.concatenate(frames).astype("float32", copy=False)
                        first = cls._split_audio(combined)[0]
                        yield first
                        remaining = combined[len(first):].copy()
                        frames, buffered = [remaining], len(remaining)
            for frame in container.decode(audio=0):
                yield from consume(resampler.resample(frame))
            yield from consume(resampler.resample(None))
        if buffered:
            yield from cls._split_audio(np.concatenate(frames).astype("float32", copy=False))

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
            progress_callback(0.02, "正在准备识别。")
        duration = _safe_duration(path)
        recognizer = self._recognizer()
        transcript_parts = []
        timeline_parts = []
        for start_sample, chunk in self._iter_timed_chunks(path):
            stream = recognizer.create_stream()
            stream.accept_waveform(self.sample_rate, chunk)
            recognizer.decode_stream(stream)
            text = str(stream.result.text or "").strip()
            end_sample = start_sample + len(chunk)
            if text:
                transcript_parts.append(text)
                timeline_parts.append(
                    f"{start_sample / self.sample_rate:.3f}s -> {end_sample / self.sample_rate:.3f}s {text}"
                )
            if progress_callback is not None:
                ratio, detail = _recognition_progress(end_sample / self.sample_rate, duration)
                progress_callback(ratio, detail)
        if progress_callback is not None:
            progress_callback(1.0, "识别完成，正在整理原文。")
        transcript = "\n".join(transcript_parts)
        _transcription_runtime.update(active_provider=self.name, fallback_active=False,
                                      actual_device="cpu", compute_type="int8")
        return TranscriptionResult(
            provider=self.name,
            transcript_text=transcript,
            timeline_text="\n".join(timeline_parts),
            language=None,
            segment_count=len(transcript_parts),
        )


class LocalSenseVoiceCudaProvider(LocalSenseVoiceProvider):
    name = "local_sensevoice_cuda"

    def __init__(self, model_dir: Path, sdk_path: Path):
        super().__init__(model_dir)
        from app.sensevoice_cuda import get_worker
        self.worker = get_worker(model_dir, sdk_path)

    def transcribe(self, media_file_path: str, *, capture_id: str, output_folder: str | Path,
                   output_stem: str = "raw_transcript", source_url: str | None = None,
                   progress_callback: TranscriptionProgressCallback | None = None) -> TranscriptionResult:
        path = Path(media_file_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if progress_callback:
            progress_callback(0.02, "正在准备本地显卡识别。")
        self.worker.warm()
        from itertools import islice
        chunks = iter(self._iter_timed_chunks(path))
        duration = _safe_duration(path)
        texts, timeline = [], []
        offset = 0
        while batch := list(islice(chunks, 4)):
            results = self.worker.decode([samples for _, samples in batch])
            if len(results) != len(batch):
                raise RuntimeError("显卡转写返回不完整，请重试。")
            for (start, chunk), text in zip(batch, results):
                end = start + len(chunk)
                if text:
                    texts.append(text)
                    timeline.append(f"{start/self.sample_rate:.3f}s -> {end/self.sample_rate:.3f}s {text}")
                offset = max(offset, end)
            if progress_callback:
                ratio, detail = _recognition_progress(offset / self.sample_rate, duration)
                progress_callback(ratio, detail)
        if not offset:
            raise RuntimeError("媒体文件中没有可识别的音频内容。")
        if progress_callback:
            progress_callback(1.0, "识别完成，正在整理原文。")
        _transcription_runtime.update(active_provider=self.name, fallback_active=False,
                                      actual_device="cuda", compute_type="float32")
        return TranscriptionResult(provider=self.name, transcript_text="\n".join(texts),
                                   timeline_text="\n".join(timeline), segment_count=len(texts))


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
        if DEPLOYMENT_MODE == "cloud_preview":
            raise RuntimeError("当前云端预览未配置转写服务。请配置 OpenAI-compatible 转写服务后再处理音视频。")
        raise RuntimeError("识别组件还没有准备好。请回到首页，按提示下载识别组件后再试。")


class FallbackTranscriptionProvider:
    def __init__(self, primary: TranscriptionProvider, fallback: TranscriptionProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}_with_{fallback.name}_fallback"

    def transcribe(self, media_file_path: str, **kwargs) -> TranscriptionResult:
        try:
            result = self.primary.transcribe(media_file_path, **kwargs)
            _transcription_runtime["active_provider"] = self.primary.name
            _transcription_runtime["fallback_active"] = False
            return result
        except Exception:
            progress_callback = kwargs.get("progress_callback")
            if progress_callback is not None:
                progress_callback(0.05, "加速引擎暂不可用，已切换兼容模式。")
            result = self.fallback.transcribe(media_file_path, **kwargs)
            _transcription_runtime["active_provider"] = self.fallback.name
            _transcription_runtime["fallback_active"] = True
            return result


_transcription_runtime: dict[str, object] = {
    "active_provider": None,
    "fallback_active": False,
    "actual_device": None,
    "compute_type": None,
}


def _installed_version(distribution: str) -> str | None:
    try:
        return package_version(distribution)
    except PackageNotFoundError:
        return None


def transcription_runtime_status(*, platform: str = "") -> dict[str, object]:
    from app.local_preferences import transcription_options
    from scripts.config import COMPUTE_TYPE
    import os
    options = transcription_options(device=DEVICE, model_path=MODEL_PATH, compute_type=COMPUTE_TYPE,
                                    desktop=os.environ.get("AUDIO2TEXT_RUNTIME_TARGET") == "windows_desktop")
    provider = get_transcription_provider(platform=platform)
    primary = provider.primary if isinstance(provider, FallbackTranscriptionProvider) else provider
    fallback = provider.fallback if isinstance(provider, FallbackTranscriptionProvider) else None
    if isinstance(primary, LocalFasterWhisperProvider):
        model = options["model_path"].name
        engine_version = _installed_version("faster-whisper")
    elif isinstance(primary, LocalSenseVoiceProvider):
        model = "SenseVoiceSmall-fp32" if isinstance(primary, LocalSenseVoiceCudaProvider) else "SenseVoiceSmall-int8"
        engine_version = _installed_version("sherpa-onnx")
    elif isinstance(primary, OpenAICompatibleTranscriptionProvider):
        model = primary.model
        engine_version = "api"
    elif isinstance(primary, CloudflareWorkersAIWhisperProvider):
        model = "@cf/openai/whisper"
        engine_version = "workers-ai"
    else:
        model = None
        engine_version = None
    return {
        "configured_provider": TRANSCRIPTION_PROVIDER,
        "selected_provider": primary.name,
        "active_provider": _transcription_runtime["active_provider"] or primary.name,
        "model": model,
        "engine_version": engine_version,
        "fallback_provider": getattr(fallback, "name", None),
        "fallback_active": bool(_transcription_runtime["fallback_active"]),
        "selected_device": options["device"],
        "actual_device": _transcription_runtime["actual_device"],
        "compute_type": _transcription_runtime["compute_type"],
    }


def get_transcription_provider(*, platform: str = "") -> TranscriptionProvider:
    from app.local_preferences import transcription_options
    from scripts.config import COMPUTE_TYPE
    import os
    options = transcription_options(device=DEVICE, model_path=MODEL_PATH, compute_type=COMPUTE_TYPE,
                                    desktop=os.environ.get("AUDIO2TEXT_RUNTIME_TARGET") == "windows_desktop")
    device = options.get("execution_device", options["device"])
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
    faster_whisper_ready = options["model_path"].is_dir()
    if device == "cuda":
        if options.get("engine") == "sensevoice":
            return LocalSenseVoiceCudaProvider(options["model_path"], options["sdk_path"])
        return LocalFasterWhisperProvider()
    if options.get("engine") == "sensevoice":
        return LocalSenseVoiceProvider(options["model_path"])
    if TRANSCRIPTION_PROVIDER == "local_sensevoice" and sensevoice_ready:
        return LocalSenseVoiceProvider()
    if TRANSCRIPTION_PROVIDER == "auto":
        gpu_ready = device == "auto" and _nvidia_gpu_memory_mb() >= 4096
        prefer_faster_whisper = faster_whisper_ready and gpu_ready
        if prefer_faster_whisper:
            primary = LocalFasterWhisperProvider()
            return FallbackTranscriptionProvider(primary, LocalSenseVoiceProvider()) if sensevoice_ready else primary
        if sensevoice_ready:
            return LocalSenseVoiceProvider()
        if faster_whisper_ready:
            return LocalFasterWhisperProvider()
    if TRANSCRIPTION_AVAILABLE and TRANSCRIPTION_PROVIDER == "openai_compatible":
        return UnavailableTranscriptionProvider()
    if TRANSCRIPTION_AVAILABLE:
        return LocalFasterWhisperProvider()
    return UnavailableTranscriptionProvider()


def warm_desktop_transcription():
    """Called on a background thread; API/window startup must remain responsive."""
    provider = get_transcription_provider()
    if isinstance(provider, LocalSenseVoiceCudaProvider):
        provider.worker.warm()
        _transcription_runtime.update(active_provider=provider.name, actual_device="cuda",
                                      compute_type="float32", fallback_active=False)

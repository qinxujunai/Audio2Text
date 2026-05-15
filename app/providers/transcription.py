from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import httpx

from app.settings import (
    DEFAULT_TIMEOUT_SECONDS,
    DEPLOYMENT_MODE,
    OPENAI_COMPATIBLE_API_KEY,
    OPENAI_COMPATIBLE_BASE_URL,
    OPENAI_COMPATIBLE_MODEL,
    TRANSCRIPTION_AVAILABLE,
    TRANSCRIPTION_PROVIDER,
    WORKER_TRANSCRIBE_URL,
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

    def __init__(self, *, transcribe_url: str) -> None:
        self.transcribe_url = transcribe_url

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

        with file_path.open("rb") as handle:
            with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS * 10, trust_env=False) as client:
                response = client.post(
                    self.transcribe_url,
                    files={"file": (file_path.name, handle, "application/octet-stream")},
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
    if DEPLOYMENT_MODE == "cloud_preview":
        return CloudflareWorkersAIWhisperProvider(transcribe_url=WORKER_TRANSCRIBE_URL)
    if TRANSCRIPTION_AVAILABLE and TRANSCRIPTION_PROVIDER == "openai_compatible":
        return UnavailableTranscriptionProvider()
    if TRANSCRIPTION_AVAILABLE:
        return LocalFasterWhisperProvider()
    return UnavailableTranscriptionProvider()

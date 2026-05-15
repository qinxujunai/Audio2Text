from __future__ import annotations

import hashlib
import mimetypes
import queue
import re
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import av
import httpx

from app.capture_diagnostics import CaptureDiagnostics, write_capture_event
from app.extractors import ExtractionError, ExtractionOutcome
from app.providers import TranscriptionResult, get_transcription_provider
from app.resolver import resolve_file_type, resolve_url
from app.schemas import (
    ArtifactModel,
    ContentFactItemModel,
    ProcessingStateModel,
    ResultDocumentModel,
    ResultSegmentModel,
    ResultViewsModel,
    SourceMetaModel,
    TraceEventModel,
)
from app.settings import (
    ARTIFACTS_DIR,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    FFMPEG_PATH,
    FREE_DURATION_SECONDS,
    RELAY_AUTO_START_WORKER,
    RELAY_RUN_MODE,
)
from app.source_adapters import LocalFileAdapter, get_url_adapter
from app.storage import get_capture_repository
from app.store_fs import now_iso
from scripts.logger import get_logger

logger = get_logger("capture_pipeline", "capture_pipeline.log")

repository = get_capture_repository()
transcription_provider = get_transcription_provider()
capture_queue: queue.Queue[str] = queue.Queue()
worker_started = False
worker_thread: threading.Thread | None = None
worker_lock = threading.Lock()
queued_capture_ids: set[str] = set()
active_capture_ids: set[str] = set()
asset_preparation_capture_ids: set[str] = set()

PUBLIC_ERROR_MESSAGES = {
    "input": "这条内容当前不可用。你可以换一个公开链接，或重新上传文件再试。",
    "resolve": "这条内容当前不可用。你可以换一个公开链接，或重新上传文件再试。",
    "extract": "来源识别成功，但没有稳定拿到正文、字幕、媒体或图片结果。建议换一条内容再试。",
    "download": "资源下载没有成功，请稍后重试，或换一条内容再试。",
    "transcribe": "媒体已经拿到了，但转写没有成功。建议稍后重试，或换一个更清晰的音视频文件。",
    "limit": "当前免费版仅支持 30 分钟以内的音频或视频，请换一条更短的内容再试。",
    "internal": "处理过程中发生了内部问题。请返回首页，换一条内容重新试试。",
}

_IMAGE_ARTIFACT_TYPE_PATTERN = re.compile(r"^image(?:_live)?_\d+$")


def _clean_text(value: str | None) -> str:
    return (value or "").strip()


@dataclass(frozen=True)
class _ResolvedTitle:
    text: str
    kind: str = "verbatim"


def _normalize_text_blocks(value: str | None) -> str:
    lines = [re.sub(r"[ \t]+", " ", (line or "").strip()) for line in str(value or "").splitlines()]
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if normalized and not previous_blank:
                normalized.append("")
            previous_blank = True
            continue
        normalized.append(line)
        previous_blank = False
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized).strip()


def _strip_title_noise(value: str | None) -> str:
    text = _normalize_text_blocks(value)
    if not text or text.lower().startswith(("http://", "https://")):
        return ""
    text = re.sub(r"#([^#\n]{1,80})(?:\[[^\]\n]{1,12}\])?#", " ", text)
    text = re.sub(r"(?:^|\s)#[^\s#]{1,80}", " ", text)
    text = re.sub(r"(?<!\w)@[^\s@#]{1,40}", " ", text)
    text = re.sub(r"\s*[-|·/]+\s*(小红书|抖音|哔哩哔哩|bilibili|youtube)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*-\s*你的生活兴趣社区\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -|·/：:，,")
    return text


def _truncate_display_title(value: str, *, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    shortened = text[:limit].rstrip(" ,，。！？!?;；:：、-")
    return f"{shortened}…"


def _title_fragments(value: str) -> list[str]:
    fragments: list[str] = []
    for line in re.split(r"[\r\n]+", value):
        for fragment in re.split(r"(?<=[。！？!?])\s*|(?<=[.])\s+(?=[A-Z0-9\u4e00-\u9fff])", line):
            cleaned = _strip_title_noise(fragment)
            if cleaned:
                fragments.append(cleaned)
    return fragments


def _looks_like_body_title(value: str, *, platform: str = "") -> bool:
    compact = re.sub(r"\s+", "", value)
    punctuation_count = len(re.findall(r"[。！？!?；;，,]", value))
    fragment_count = len(_title_fragments(value))
    if platform in {"douyin", "xiaohongshu"}:
        return "\n" in value or len(compact) > 36 or punctuation_count >= 2 or fragment_count >= 2
    return ("\n" in value and len(compact) > 24) or (len(compact) > 88 and (punctuation_count >= 2 or fragment_count >= 2))


def _resolve_display_title(value: str | None, platform: str = "") -> _ResolvedTitle:
    text = _strip_title_noise(value)
    if not text:
        return _ResolvedTitle("")
    if _looks_like_body_title(text, platform=platform):
        fragments = _title_fragments(text)
        if fragments:
            limit = 72 if platform in {"douyin", "xiaohongshu"} else 88
            return _ResolvedTitle(_truncate_display_title(fragments[0], limit=limit), kind="synthetic")
    limit = 96 if platform in {"douyin", "xiaohongshu"} else 140
    return _ResolvedTitle(_truncate_display_title(text, limit=limit), kind="verbatim")


def _clean_display_title(value: str | None, platform: str = "") -> str:
    return _resolve_display_title(value, platform=platform).text


def _clean_social_body_text(value: str | None) -> str:
    normalized = _normalize_text_blocks(value)
    if not normalized:
        return ""

    cleaned_lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        # Remove hashtag/topic blocks and account mentions from social captions.
        line = re.sub(r"#([^#\n]{1,80})(?:\[[^\]\n]{1,12}\])?#", " ", line)
        line = re.sub(r"(?:^|\s)#[^\s#]{1,80}", " ", line)
        line = re.sub(r"(?<!\w)@[^\s@#]{1,40}", " ", line)
        line = re.sub(r"\s+", " ", line).strip(" -|·/：:，,")
        if not line:
            continue
        if not re.search(r"[\w\u4e00-\u9fff]", line):
            continue
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    return "\n".join(cleaned_lines).strip()


def _clean_delivery_text(value: str | None, *, platform: str, origin: str) -> str:
    if origin in {"subtitle", "transcript", "ocr"}:
        return _normalize_text_blocks(value)
    if platform in {"douyin", "xiaohongshu"} and origin in {"article", "notes"}:
        return _clean_social_body_text(value)
    return _normalize_text_blocks(value)


def _normalize_heading_text(value: str | None) -> str:
    return _clean_text(value).rstrip("…").rstrip(" .,:;!?，。！？、")


def _drop_title_from_body(title: str, body: str, *, title_kind: str = "verbatim") -> str:
    cleaned_title = _normalize_heading_text(title)
    cleaned_body = _normalize_text_blocks(body)
    if not cleaned_title or not cleaned_body:
        return cleaned_body
    if title_kind != "verbatim":
        return cleaned_body
    body_lines = cleaned_body.splitlines()
    if body_lines:
        first_line = body_lines[0].strip()
        remaining_text = "\n".join(body_lines[1:]).strip()
        if remaining_text and _normalize_heading_text(first_line) == cleaned_title:
            return remaining_text
    return cleaned_body


def _drop_explicit_title_block(title: str, body: str) -> str:
    return _drop_title_from_body(title, body, title_kind="verbatim")


def _result_title_info(
    *,
    capture=None,
    source: SourceMetaModel | None = None,
    extraction: ExtractionOutcome | None = None,
) -> _ResolvedTitle:
    candidates = [
        getattr(extraction, "title", None),
        getattr(capture, "title", None),
    ]
    platform = getattr(extraction, "platform", None) or getattr(source, "platform", "") or ""
    for candidate in candidates:
        cleaned = _resolve_display_title(candidate, platform=platform)
        if cleaned.text:
            return cleaned
    return _ResolvedTitle("")


def _result_title(*, capture=None, source: SourceMetaModel | None = None, extraction: ExtractionOutcome | None = None) -> str:
    return _result_title_info(capture=capture, source=source, extraction=extraction).text


def _compose_txt_text(title: str, body: str) -> str:
    cleaned_title = _clean_text(title)
    cleaned_body = _drop_explicit_title_block(cleaned_title, body)
    if cleaned_title and cleaned_body:
        return f"{cleaned_title}\n\n{cleaned_body}".strip()
    return cleaned_body or cleaned_title


def _has_real_markdown_features(value: str | None) -> bool:
    text = _normalize_text_blocks(value)
    if not text:
        return False
    patterns = (
        r"^\s{0,3}#{1,6}\s+\S",
        r"^\s*[-*+]\s+\S",
        r"^\s*\d+\.\s+\S",
        r"^\s*>\s+\S",
        r"^\s*```",
        r"^\s*\|.+\|\s*$",
        r"\[[^\]]+\]\([^)]+\)",
    )
    return any(re.search(pattern, text, flags=re.MULTILINE) for pattern in patterns)


def _compose_markdown_text(title: str, body: str) -> str:
    cleaned_title = _clean_text(title)
    cleaned_body = _drop_explicit_title_block(cleaned_title, body)
    if not _has_real_markdown_features(cleaned_body):
        return ""
    if cleaned_title and not re.match(r"^\s{0,3}#{1,6}\s+\S", cleaned_body):
        return f"# {cleaned_title}\n\n{cleaned_body}".strip()
    return cleaned_body


def _platform_label(platform: str) -> str:
    return {
        "youtube": "YouTube",
        "bilibili": "哔哩哔哩",
        "xiaoyuzhou": "小宇宙",
        "douyin": "抖音",
        "xiaohongshu": "小红书",
        "wechat_article": "微信公众号",
        "generic_web": "网页",
        "local_file": "本地文件",
    }.get(platform, platform or "来源")


def _content_type_label(content_type: str) -> str:
    return {
        "video": "视频",
        "audio": "音频",
        "article": "文章",
        "image_article": "图文",
        "webpage": "网页",
        "unknown": "未知",
    }.get(content_type, content_type or "未知")


def _result_type_label(result_type: str) -> str:
    return {
        "subtitle": "字幕",
        "transcript": "转写",
        "article": "正文",
        "notes": "辅助文本",
        "ocr": "OCR",
        "mixed": "混合",
    }.get(result_type, result_type or "-")


def _format_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remain = total % 60
    if hours:
        return f"{hours} 小时 {minutes} 分 {remain} 秒"
    if minutes:
        return f"{minutes} 分 {remain} 秒"
    return f"{remain} 秒"


def _format_published_at(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _is_substantial_text(text: str, *, minimum_length: int = 180, minimum_lines: int = 3) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    if len(cleaned) >= minimum_length:
        return True
    if cleaned.count("\n") + 1 >= minimum_lines:
        return True
    return False


def _default_progress_for_stage(stage: str) -> int:
    return {
        "queued": 2,
        "resolve": 5,
        "extract": 12,
        "transcribe": 55,
        "compose": 94,
        "completed": 100,
        "failed": 0,
    }.get(stage, 0)


def _fast_path_detail(extraction: ExtractionOutcome) -> str:
    if _clean_text(extraction.subtitle_text):
        return "已拿到可用字幕，正在跳过转写并整理结果。"
    if extraction.content_type != "video" and _clean_text(extraction.article_text):
        return "已拿到正文内容，正在整理结果。"
    if "browser_session" in extraction.strategy:
        return "浏览器会话提取已完成，正在整理结果。"
    return "正在整理最终结果。"


def _make_trace(
    stage: str,
    message: str,
    *,
    level: str = "info",
    provider: str | None = None,
    detail: str | None = None,
) -> TraceEventModel:
    return TraceEventModel(
        at=now_iso(),
        stage=stage,
        level=level,
        message=message,
        provider=provider,
        detail=detail,
    )


def _parse_trace_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _recovery_seconds_from_trace_events(capture) -> float:
    events = list(getattr(getattr(capture, "processing", None), "trace_events", []) or [])
    total = 0.0
    previous_time: datetime | None = None
    for event in events:
        event_time = _parse_trace_time(getattr(event, "at", None))
        stage = getattr(event, "stage", "")
        message = getattr(event, "message", "")
        if event_time and previous_time and stage == "queued" and "恢复" in message:
            total += max((event_time - previous_time).total_seconds(), 0.0)
        if event_time:
            previous_time = event_time
    return total


def _unique_append(values: list[str], item: str) -> list[str]:
    if item and item not in values:
        values.append(item)
    return values


def _artifact_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "text/markdown"
    if suffix in {".mp4", ".m4v"}:
        return "video/mp4"
    if suffix == ".mov":
        return "video/quicktime"
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mkv":
        return "video/x-matroska"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _artifact_entry(capture_id: str, artifact_type: str, label: str, path: Path) -> ArtifactModel:
    return ArtifactModel(
        type=artifact_type,
        label=label,
        path=str(path),
        download_url=f"/v1/captures/{capture_id}/artifacts/{artifact_type}",
        mime_type=_artifact_mime_type(path),
        size_bytes=path.stat().st_size if path.exists() else None,
    )


def _write_text_artifact(
    capture_id: str,
    artifact_dir: Path,
    artifact_type: str,
    file_name: str,
    content: str,
    label: str,
) -> ArtifactModel | None:
    if not content.strip():
        return None
    target = artifact_dir / file_name
    target.write_text(content, encoding="utf-8")
    return _artifact_entry(capture_id, artifact_type, label, target)


def _download_remote_asset(url: str, target_path: Path, *, referer: str | None = None, accept: str = "*/*") -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": referer or url,
        "Accept": accept,
    }
    try:
        with httpx.Client(
            follow_redirects=True,
            headers=headers,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(target_path, "wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
    except httpx.HTTPError as exc:
        raise ExtractionError("download", f"资源下载失败：{exc}") from exc
    return target_path


def _download_image(url: str, target_path: Path, *, referer: str | None = None) -> Path:
    return _download_remote_asset(
        url,
        target_path,
        referer=referer,
        accept="image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    )


def _image_extension(url: str) -> str:
    path = Path(url.split("?")[0])
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return ".jpg"


def _live_photo_extension(url: str) -> str:
    path = Path(url.split("?")[0])
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".m4v"}:
        return suffix
    return ".mp4"


def _image_content_dedupe_key(path: Path) -> str:
    try:
        with av.open(str(path)) as container:
            frame = next(container.decode(video=0))
            tiny = frame.reformat(width=16, height=16, format="gray")
            pixels = tiny.to_ndarray()
            average = float(pixels.mean())
            bits = "".join("1" if int(value) >= average else "0" for value in pixels.flatten())
            return f"visual:{hashlib.sha256(bits.encode('ascii')).hexdigest()}"
    except Exception:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"bytes:{digest.hexdigest()}"


def _persist_image_artifacts(
    capture,
    artifact_dir: Path,
    image_urls: list[str],
    live_photo_video_urls: list[str],
    *,
    max_images: int | None = None,
    include_zip: bool = True,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ArtifactModel]:
    artifacts: list[ArtifactModel] = []
    saved_paths: list[Path] = []
    referer = capture.source.canonical_url or capture.url or None
    seen_image_keys: set[str] = set()
    total_images = min(len(image_urls), max_images or 20, 20)
    completed_images = 0

    for index, image_url in enumerate(image_urls[:total_images], start=1):
        target = artifact_dir / f"image_{index:02d}{_image_extension(image_url)}"
        try:
            _download_image(image_url, target, referer=referer)
        except ExtractionError:
            completed_images += 1
            if progress_callback is not None:
                progress_callback(completed_images, total_images)
            continue

        image_key = _image_content_dedupe_key(target)
        if image_key in seen_image_keys:
            target.unlink(missing_ok=True)
            completed_images += 1
            if progress_callback is not None:
                progress_callback(completed_images, total_images)
            continue

        seen_image_keys.add(image_key)
        saved_paths.append(target)
        live_url = (live_photo_video_urls[index - 1] if index - 1 < len(live_photo_video_urls) else "").strip()
        if live_url:
            live_target = artifact_dir / f"image_{index:02d}_live{_live_photo_extension(live_url)}"
            try:
                _download_remote_asset(live_url, live_target, referer=referer)
            except ExtractionError:
                live_url = ""
            else:
                saved_paths.append(live_target)
                artifacts.append(_artifact_entry(capture.id, f"image_live_{index:02d}", f"Live 图片 {index}", live_target))
        artifacts.append(_artifact_entry(capture.id, f"image_{index:02d}", f"图片 {index}", target))
        completed_images += 1
        if progress_callback is not None:
            progress_callback(completed_images, total_images)

    if include_zip and len(saved_paths) >= 2:
        zip_path = artifact_dir / "images.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in saved_paths:
                archive.write(item, arcname=item.name)
        artifacts.append(_artifact_entry(capture.id, "images_zip", "全部图片", zip_path))

    return artifacts


def _is_image_artifact_type(artifact_type: str) -> bool:
    return artifact_type == "images_zip" or bool(_IMAGE_ARTIFACT_TYPE_PATTERN.match(artifact_type))


def _merge_image_artifacts(result: ResultDocumentModel, image_artifacts: list[ArtifactModel]) -> ResultDocumentModel:
    preserved = [artifact for artifact in result.artifacts if not _is_image_artifact_type(artifact.type)]
    result.artifacts = [*preserved, *image_artifacts]
    result.views = _build_views(result)
    return result


def _persist_source_media_artifact(capture, media_file_path: str | None) -> ArtifactModel | None:
    if capture.source.content_type != "video":
        return None
    if not media_file_path:
        return None

    target = Path(media_file_path)
    if not target.exists() or not target.is_file():
        return None
    return _artifact_entry(capture.id, "source_media", "原视频", target)


def _probe_video_stream_profile(path: Path) -> tuple[str, str, str]:
    try:
        with av.open(str(path)) as container:
            container_name = str(getattr(getattr(container, "format", None), "name", "") or "").lower()
            video_codec = ""
            audio_codec = ""
            for stream in container.streams:
                codec_name = str(getattr(getattr(stream, "codec_context", None), "name", "") or "").lower()
                if stream.type == "video" and not video_codec:
                    video_codec = codec_name
                elif stream.type == "audio" and not audio_codec:
                    audio_codec = codec_name
            return container_name, video_codec, audio_codec
    except Exception:
        return "", "", ""


def _source_media_needs_preview(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".webm", ".mkv"}:
        return True

    container_name, video_codec, audio_codec = _probe_video_stream_profile(path)
    risky_video_codecs = {"av1", "libdav1d", "vp9", "vp09", "hevc", "h265", "h265_nvenc"}
    friendly_video_codecs = {"h264", "avc1"}
    friendly_audio_codecs = {"aac", "mp3", "mp4a"}

    if video_codec in risky_video_codecs:
        return True
    if suffix == ".mov" and video_codec and video_codec not in friendly_video_codecs:
        return True
    if suffix in {".mp4", ".m4v"}:
        if video_codec and video_codec not in friendly_video_codecs:
            return True
        if audio_codec and audio_codec not in friendly_audio_codecs:
            return True
        return False
    if container_name and not any(token in container_name for token in ("mp4", "mov")):
        return True
    return False


def _generate_preview_media(source_path: Path, target_path: Path) -> Path | None:
    if not FFMPEG_PATH.exists():
        return None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.unlink(missing_ok=True)
    command = [
        str(FFMPEG_PATH),
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-g",
        "48",
        "-keyint_min",
        "48",
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(target_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not target_path.exists():
        target_path.unlink(missing_ok=True)
        logger.warning("preview media transcode failed for %s: %s", source_path, (result.stderr or result.stdout or "").strip())
        return None
    return target_path


def _persist_preview_media_artifact(capture, artifact_dir: Path, media_file_path: str | None) -> ArtifactModel | None:
    if capture.source.content_type != "video" or not media_file_path:
        return None

    source_path = Path(media_file_path)
    if not source_path.exists() or not source_path.is_file():
        return None
    if not _source_media_needs_preview(source_path):
        return None

    preview_path = artifact_dir / "preview_media.mp4"
    generated = _generate_preview_media(source_path, preview_path)
    if generated is None:
        return None
    return _artifact_entry(capture.id, "preview_media", "预览视频", generated)


def _probe_media_delivery_profile(path: Path) -> tuple[str, str, str, str]:
    try:
        with av.open(str(path)) as container:
            container_name = str(getattr(getattr(container, "format", None), "name", "") or "").lower()
            video_codec = ""
            audio_codec = ""
            resolution = ""
            for stream in container.streams:
                codec_name = str(getattr(getattr(stream, "codec_context", None), "name", "") or "").lower()
                if stream.type == "video" and not video_codec:
                    video_codec = codec_name
                    width = getattr(getattr(stream, "codec_context", None), "width", None)
                    height = getattr(getattr(stream, "codec_context", None), "height", None)
                    if width and height:
                        resolution = f"{width}x{height}"
                elif stream.type == "audio" and not audio_codec:
                    audio_codec = codec_name
            return container_name, video_codec, audio_codec, resolution
    except Exception:
        return "", "", "", ""


def _video_artifact_delivery_traces(result: ResultDocumentModel, media_file_path: str | None) -> list[TraceEventModel]:
    if not media_file_path:
        return []
    source_path = Path(media_file_path)
    if not source_path.exists():
        return []
    if not any(artifact.type == "source_media" for artifact in result.artifacts):
        return []

    container_name, video_codec, audio_codec, resolution = _probe_media_delivery_profile(source_path)
    preview_generated = any(artifact.type == "preview_media" for artifact in result.artifacts)
    detail = (
        f"resolution={resolution or '-'}, "
        f"container={container_name or '-'}, "
        f"video_codec={video_codec or '-'}, "
        f"audio_codec={audio_codec or '-'}, "
        f"preview_media={'yes' if preview_generated else 'no'}"
    )
    return [
        _make_trace(
            "compose",
            "视频交付文件已生成。",
            provider="artifact_pipeline",
            detail=detail,
        )
    ]


def _paragraph_segments(text: str, *, origin: str, kind: str = "text") -> list[ResultSegmentModel]:
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    if not blocks:
        blocks = [line.strip() for line in text.splitlines() if line.strip()]
    return [ResultSegmentModel(kind=kind, origin=origin, text=block) for block in blocks]


def _timed_segments(text: str, *, origin: str) -> list[ResultSegmentModel]:
    segments: list[ResultSegmentModel] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or "->" not in stripped:
            continue
        try:
            start_chunk, remainder = stripped.split("->", 1)
            end_chunk, text_chunk = remainder.strip().split(" ", 1)
            start_sec = float(start_chunk.strip().rstrip("s"))
            end_sec = float(end_chunk.strip().rstrip("s"))
            segments.append(
                ResultSegmentModel(
                    kind="timed_text",
                    origin=origin,
                    text=text_chunk.strip(),
                    start_sec=start_sec,
                    end_sec=end_sec,
                )
            )
        except ValueError:
            continue
    return segments


def _build_segments(result: ResultDocumentModel) -> list[ResultSegmentModel]:
    segments: list[ResultSegmentModel] = []
    if result.timeline_text:
        segments.extend(_timed_segments(result.timeline_text, origin="transcript"))
    if not segments and result.transcript_text:
        segments.extend(_paragraph_segments(result.transcript_text, origin="transcript"))
    if result.article_text:
        segments.extend(_paragraph_segments(result.article_text, origin="article"))
    if result.notes_text:
        segments.extend(_paragraph_segments(result.notes_text, origin="notes", kind="note"))
    if result.ocr_text:
        segments.extend(_paragraph_segments(result.ocr_text, origin="ocr", kind="ocr"))
    return segments


def _build_views(result: ResultDocumentModel) -> ResultViewsModel:
    return ResultViewsModel(
        primary=result.primary_text,
        transcript=result.transcript_text,
        subtitle=result.subtitle_text,
        article=result.article_text,
        notes=result.notes_text,
        timeline=result.timeline_text,
        markdown=result.markdown_text,
        json="",
        meta=result.meta_text,
        ocr=result.ocr_text,
    )


def _build_content_facts(capture, source: SourceMetaModel, result: ResultDocumentModel) -> list[ContentFactItemModel]:
    facts: list[ContentFactItemModel] = []

    def append(key: str, label: str, value: str | None, href: str | None = None) -> None:
        cleaned = _clean_text(value)
        if not cleaned:
            return
        facts.append(ContentFactItemModel(key=key, label=label, value=cleaned, href=href))

    append("title", "标题", _result_title(capture=capture, source=source) or source.canonical_url)
    append("platform", "平台", _platform_label(source.platform))
    append("content_type", "内容类型", _content_type_label(source.content_type))
    append("author", "作者", source.author)
    append("published_at", "发布时间", _format_published_at(source.published_at))
    append("duration", "时长", _format_duration(source.duration_seconds))
    if source.image_urls:
        append("image_count", "图片数量", str(len(source.image_urls)))
    return facts


def _select_primary_result(extraction: ExtractionOutcome, transcript_text: str) -> tuple[str, str, str, str, str]:
    transcript = _clean_delivery_text(transcript_text, platform=extraction.platform, origin="transcript")
    subtitle = _clean_delivery_text(extraction.subtitle_text, platform=extraction.platform, origin="subtitle")
    article = _clean_delivery_text(extraction.article_text, platform=extraction.platform, origin="article")
    notes = _clean_delivery_text(extraction.notes_text or extraction.description, platform=extraction.platform, origin="notes")
    ocr = _clean_delivery_text(extraction.ocr_text, platform=extraction.platform, origin="ocr")

    if extraction.content_type == "audio":
        if subtitle:
            return subtitle, "subtitle", "high", "full", "subtitle"
        if transcript:
            return transcript, "transcript", "high", "full", "transcript"
        if extraction.platform == "xiaoyuzhou":
            return "", "", "low", "partial", "none"
        if article:
            article_substantial = _is_substantial_text(article)
            return article, "article", "medium" if article_substantial else "low", "substantial" if article_substantial else "partial", "article"
        if notes:
            notes_substantial = _is_substantial_text(notes)
            return notes, "notes", "medium" if notes_substantial else "low", "substantial" if notes_substantial else "partial", "notes"
        return "", "", "low", "partial", "none"

    if extraction.content_type == "video":
        if subtitle:
            return subtitle, "subtitle", "high", "full", "subtitle"
        if transcript:
            return transcript, "transcript", "high", "full", "transcript"
        return "", "", "low", "partial", "none"

    if article:
        is_substantial_article = len(article) >= 180 or article.count("\n") >= 2
        return article, "article", "medium" if is_substantial_article else "low", "substantial" if is_substantial_article else "partial", "article"
    if notes:
        return notes, "notes", "medium", "substantial", "notes"
    if ocr:
        return ocr, "ocr", "low", "partial", "ocr"
    return "", "", "low", "partial", "none"


def _select_silent_video_fallback(
    extraction: ExtractionOutcome,
    transcription: TranscriptionResult | None,
) -> tuple[str, str, str]:
    if extraction.content_type != "video":
        return "", "", "none"
    if _clean_text(extraction.subtitle_text):
        return "", "", "none"
    if transcription is None:
        return "", "", "none"
    if _clean_text(transcription.transcript_text):
        return "", "", "none"
    if int(getattr(transcription, "segment_count", 0) or 0) > 0:
        return "", "", "none"

    article = _clean_delivery_text(extraction.article_text, platform=extraction.platform, origin="article")
    notes = _clean_delivery_text(extraction.notes_text or extraction.description, platform=extraction.platform, origin="notes")

    if extraction.platform in {"douyin", "xiaohongshu"}:
        social_copy = _clean_social_fallback_text(extraction)
        if _is_substantial_text(social_copy, minimum_length=24, minimum_lines=1):
            source = "article" if article else "notes"
            return social_copy, source or "notes", source or "notes"

    if article and _is_substantial_text(article, minimum_length=24, minimum_lines=1):
        return article, "article", "article"
    if notes and _is_substantial_text(notes, minimum_length=24, minimum_lines=1):
        return notes, "notes", "notes"
    return "", "", "none"


def _clean_social_fallback_text(extraction: ExtractionOutcome) -> str:
    if extraction.platform not in {"douyin", "xiaohongshu"}:
        return ""

    notes = _clean_delivery_text(extraction.notes_text or extraction.description, platform=extraction.platform, origin="notes")
    article = _clean_delivery_text(extraction.article_text, platform=extraction.platform, origin="article")
    merged = article or notes
    if len(merged) < 8:
        return ""
    return merged[:600]


def _ensure_final_result(source: SourceMetaModel, result: ResultDocumentModel) -> None:
    primary = _clean_text(result.primary_text)
    if source.content_type in {"audio", "video"}:
        if primary:
            return
        raise ExtractionError("transcribe", "没有拿到可用的转写结果。")

    if source.content_type == "image_article":
        if source.image_urls or primary:
            return
        raise ExtractionError("extract", "图文内容没有提取到可用正文或图片。")

    if source.platform == "wechat_article":
        if primary or source.image_urls:
            return
        raise ExtractionError("extract", "微信公众号文章没有提取到可用正文或图片。")


def _transcript_status(extraction: ExtractionOutcome, transcription: TranscriptionResult | None) -> str:
    if _clean_text(extraction.subtitle_text):
        return "subtitle"
    if transcription and _clean_text(transcription.transcript_text):
        return "transcribed"
    fallback_text, _, _ = _select_silent_video_fallback(extraction, transcription)
    if fallback_text:
        return "skipped"
    if extraction.needs_transcription:
        return "failed"
    return "skipped"


def _selected_result_language(
    *,
    source: SourceMetaModel,
    extraction: ExtractionOutcome,
    transcription: TranscriptionResult | None,
    text_source: str,
) -> str:
    if text_source == "transcript":
        return _clean_text(
            (getattr(transcription, "language", "") if transcription else "")
            or extraction.selected_language
            or extraction.language
            or source.language
        )
    if text_source == "subtitle":
        return _clean_text(extraction.selected_language or extraction.language or source.language)
    if text_source in {"article", "notes", "ocr"}:
        return _clean_text(extraction.language or source.language)
    return ""


def _provider_trace_events(
    *,
    extraction: ExtractionOutcome | None = None,
    exc: ExtractionError | None = None,
) -> list[TraceEventModel]:
    entries = extraction.provider_traces if extraction is not None else (exc.provider_traces if exc is not None else [])
    return [
        _make_trace(
            item.stage,
            item.message,
            level=item.level,
            provider=item.provider,
            detail=item.detail,
        )
        for item in entries
    ]


def _merge_result(
    capture,
    source: SourceMetaModel,
    extraction: ExtractionOutcome,
    transcription: TranscriptionResult | None,
) -> ResultDocumentModel:
    title_info = _result_title_info(capture=capture, source=source, extraction=extraction)
    resolved_title = title_info.text
    transcript_text = _drop_title_from_body(
        resolved_title,
        _clean_delivery_text(
            transcription.transcript_text if transcription else "",
            platform=extraction.platform,
            origin="transcript",
        ),
        title_kind=title_info.kind,
    )
    timeline_text = _clean_text(transcription.timeline_text if transcription else "") or _clean_text(extraction.subtitle_timeline_text)
    primary_text, result_type, confidence, completeness, text_source = _select_primary_result(extraction, transcript_text)
    fallback_text, fallback_result_type, fallback_text_source = _select_silent_video_fallback(extraction, transcription)
    if not primary_text and fallback_text:
        primary_text = fallback_text
        result_type = fallback_result_type
        text_source = fallback_text_source
        confidence = "medium"
        completeness = "partial"

    primary_body = _drop_title_from_body(resolved_title, primary_text, title_kind=title_info.kind)
    subtitle_text = _drop_title_from_body(
        resolved_title,
        _clean_delivery_text(extraction.subtitle_text, platform=extraction.platform, origin="subtitle"),
        title_kind=title_info.kind,
    )
    article_body = _drop_title_from_body(
        resolved_title,
        _clean_delivery_text(extraction.article_text, platform=extraction.platform, origin="article"),
        title_kind=title_info.kind,
    )
    notes_body = _drop_title_from_body(
        resolved_title,
        _clean_delivery_text(extraction.notes_text or extraction.description, platform=extraction.platform, origin="notes"),
        title_kind=title_info.kind,
    )
    ocr_text = _drop_title_from_body(
        resolved_title,
        _clean_delivery_text(extraction.ocr_text, platform=extraction.platform, origin="ocr"),
        title_kind=title_info.kind,
    )
    article_text = article_body if text_source == "article" or source.content_type not in {"audio", "video"} else ""
    notes_text = notes_body if text_source == "notes" or source.content_type == "audio" else ""
    if source.platform == "xiaoyuzhou":
        notes_text = ""
    media_ready = bool(extraction.media_file_path and Path(extraction.media_file_path).exists())
    result_notice = ""
    if fallback_text:
        result_notice = "未检测到可转写语音，已改为交付页面文案与原视频。"
    elif source.content_type == "video" and primary_body and not media_ready:
        result_notice = "文本已整理完成，原视频暂未成功下载。"
    markdown_text = _compose_markdown_text(resolved_title, primary_body)

    result = ResultDocumentModel(
        primary_text=primary_body,
        primary_result_type=result_type,
        source_item_id=source.source_item_id or extraction.source_item_id or "",
        cache_hit=False,
        extraction_path=list(extraction.strategy),
        confidence=confidence,  # type: ignore[arg-type]
        completeness=completeness,  # type: ignore[arg-type]
        transcript_status=_transcript_status(extraction, transcription),  # type: ignore[arg-type]
        text_source=text_source,  # type: ignore[arg-type]
        subtitle_source=extraction.subtitle_source or "none",  # type: ignore[arg-type]
        selected_language=_selected_result_language(
            source=source,
            extraction=extraction,
            transcription=transcription,
            text_source=text_source,
        ),
        result_notice=result_notice,
        transcript_text=transcript_text,
        timeline_text=timeline_text,
        markdown_text=markdown_text,
        meta_text="",
        subtitle_text=subtitle_text,
        article_text=article_text,
        notes_text=notes_text,
        ocr_text=ocr_text,
    )
    result.segments = _build_segments(result)
    result.content_facts = _build_content_facts(capture, source, result)
    result.views = _build_views(result)
    return result


def _persist_result_artifacts(
    capture,
    result: ResultDocumentModel,
    *,
    media_file_path: str | None = None,
    include_images: bool = True,
    image_preview_limit: int = 0,
    progress_callback: Callable[[int, str], None] | None = None,
) -> ResultDocumentModel:
    artifact_dir = ARTIFACTS_DIR / capture.id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    result.content_facts = _build_content_facts(capture, capture.source, result)
    artifacts: list[ArtifactModel] = []
    result_title = _result_title(capture=capture, source=capture.source)

    text_artifact = _write_text_artifact(
        capture.id,
        artifact_dir,
        "txt",
        "capture.txt",
        _compose_txt_text(result_title, result.primary_text),
        "文字结果",
    )
    if text_artifact is not None:
        artifacts.append(text_artifact)

    markdown_artifact = _write_text_artifact(
        capture.id,
        artifact_dir,
        "md",
        "capture.md",
        result.markdown_text,
        "Markdown",
    )
    if markdown_artifact is not None:
        artifacts.append(markdown_artifact)

    source_media_artifact = _persist_source_media_artifact(capture, media_file_path)
    if source_media_artifact is not None:
        artifacts.append(source_media_artifact)
    preview_media_artifact = _persist_preview_media_artifact(capture, artifact_dir, media_file_path)
    if preview_media_artifact is not None:
        artifacts.append(preview_media_artifact)

    if include_images:
        image_count = min(len(capture.source.image_urls or []), 20)
        if image_count and progress_callback is not None:
            progress_callback(97, f"正文已整理完成，正在下载图片（0/{image_count}）。")

        artifacts.extend(
            _persist_image_artifacts(
                capture,
                artifact_dir,
                capture.source.image_urls,
                capture.source.live_photo_video_urls,
                progress_callback=(
                    None
                    if progress_callback is None or image_count <= 0
                    else lambda completed, total: progress_callback(
                        97 + min(1, int((completed / max(total, 1)) * 2)),
                        f"正文已整理完成，正在下载图片（{completed}/{total}）。",
                    )
                ),
            )
        )
        if image_count >= 2 and progress_callback is not None:
            progress_callback(99, "正文与图片已整理完成，正在打包下载文件。")
    elif image_preview_limit > 0:
        artifacts.extend(
            _persist_image_artifacts(
                capture,
                artifact_dir,
                capture.source.image_urls,
                capture.source.live_photo_video_urls,
                max_images=image_preview_limit,
                include_zip=False,
            )
        )
    result.artifacts = artifacts
    result.views = _build_views(result)
    return result


def _persist_result_image_artifacts(
    capture,
    result: ResultDocumentModel,
    *,
    progress_callback: Callable[[int, str], None] | None = None,
) -> ResultDocumentModel:
    artifact_dir = ARTIFACTS_DIR / capture.id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    image_count = min(len(capture.source.image_urls or []), 20)
    image_artifacts = _persist_image_artifacts(
        capture,
        artifact_dir,
        capture.source.image_urls,
        capture.source.live_photo_video_urls,
        progress_callback=(
            None
            if progress_callback is None or image_count <= 0
            else lambda completed, total: progress_callback(
                97 + min(1, int((completed / max(total, 1)) * 2)),
                f"正文已整理完成，正在下载图片（{completed}/{total}）。",
            )
        ),
    )
    if image_count >= 2 and progress_callback is not None:
        progress_callback(99, "正文与图片已整理完成，正在打包下载文件。")
    return _merge_image_artifacts(result, image_artifacts)


def _should_prepare_images_async(source: SourceMetaModel) -> bool:
    return bool(source.image_urls)


def _start_asset_preparation(capture_id: str) -> None:
    with worker_lock:
        if capture_id in asset_preparation_capture_ids:
            return
        asset_preparation_capture_ids.add(capture_id)
    thread = threading.Thread(target=_prepare_image_assets_in_background, args=(capture_id,), daemon=True)
    thread.start()


def _prepare_image_assets_in_background(capture_id: str) -> None:
    def persist_capture(*, processing=None, **fields):
        return repository.update(capture_id, processing=processing, **fields)

    def update_pending_progress(percent: int, detail: str) -> None:
        current = repository.get(capture_id)
        if current is None or not current.asset_preparation_pending:
            return
        persist_capture(
            processing=_update_processing(
                current,
                current_stage="completed",
                progress_percent=percent,
                progress_detail=detail,
                reset_failure_state=True,
            )
        )

    try:
        capture = repository.get(capture_id)
        if capture is None or not capture.asset_preparation_pending or capture.result is None:
            return

        if capture.source.image_urls:
            update_pending_progress(97, f"正文已整理完成，正在下载图片（0/{min(len(capture.source.image_urls), 20)}）。")

        persisted_result = _persist_result_image_artifacts(
            capture,
            capture.result,
            progress_callback=update_pending_progress,
        )
        current = repository.get(capture_id) or capture
        persist_capture(
            asset_preparation_pending=False,
            result=persisted_result,
            processing=_update_processing(
                current,
                current_stage="completed",
                progress_percent=100,
                progress_detail="处理完成，结果已可查看和下载。",
                reset_failure_state=True,
                trace=[_make_trace("compose", "图片下载与打包已补齐。", provider="artifact_pipeline")],
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Image asset preparation failed for %s: %s", capture_id, exc)
        current = repository.get(capture_id)
        if current is not None and current.asset_preparation_pending:
            persist_capture(
                asset_preparation_pending=False,
                processing=_update_processing(
                    current,
                    current_stage="completed",
                    progress_percent=100,
                    progress_detail="结果已可查看，部分下载文件暂未准备完成。",
                    warnings=["部分图片下载文件暂未准备完成。"],
                    trace=[
                        _make_trace(
                            "compose",
                            "图片下载文件未能全部补齐。",
                            level="warning",
                            provider="artifact_pipeline",
                            detail=str(exc),
                        )
                    ],
                ),
            )
    finally:
        with worker_lock:
            asset_preparation_capture_ids.discard(capture_id)


def _update_processing(
    capture,
    *,
    current_stage: str,
    progress_percent: int | None = None,
    progress_detail: str | None = None,
    completed_stage: str | None = None,
    warnings: list[str] | None = None,
    strategies: list[str] | None = None,
    trace: list[TraceEventModel] | None = None,
    failure_reason_code: str | None = None,
    retryable: bool | None = None,
    reset_failure_state: bool = False,
) -> ProcessingStateModel:
    processing = capture.processing.model_copy(deep=True)
    processing.current_stage = current_stage
    processing.progress_percent = _default_progress_for_stage(current_stage) if progress_percent is None else progress_percent
    if progress_detail is not None:
        processing.progress_detail = progress_detail
    if reset_failure_state:
        processing.failure_reason_code = None
        processing.retryable = False
    if completed_stage:
        _unique_append(processing.completed_stages, completed_stage)
    for warning in warnings or []:
        _unique_append(processing.warnings, warning)
    for item in strategies or []:
        _unique_append(processing.extraction_strategy, item)
    for event in trace or []:
        processing.trace_events.append(event)
    if failure_reason_code is not None:
        processing.failure_reason_code = failure_reason_code
    if retryable is not None:
        processing.retryable = retryable
    return processing


def _is_media_content(content_type: str) -> bool:
    return content_type in {"audio", "video"}


def _public_error_message(stage: str, raw_message: str | None = None) -> str:
    return _public_error_message_with_reason(stage, None, raw_message)


def _public_error_message_with_reason(stage: str, reason_code: str | None = None, raw_message: str | None = None) -> str:
    if reason_code == "youtube_video_unavailable":
        return "这条 YouTube 视频当前不可访问、已下架，或不再公开。请换一条公开视频再试。"
    if reason_code == "youtube_transcript_unavailable":
        return "这条 YouTube 视频当前没有可用字幕，媒体回退也未成功。请稍后再试，或换一条带字幕的视频。"
    if reason_code in {"youtube_extract_timeout", "youtube_extract_network_failed"}:
        return "当前网络环境没有稳定连上 YouTube。请稍后重试，或更换可访问 YouTube 的网络后再试。"
    if reason_code == "bilibili_video_invalid":
        return "这条哔哩哔哩链接无效，或内容已经不可访问。请换一条公开视频再试。"
    if reason_code == "source_not_found":
        return "这条内容当前已经不可访问，或页面已经失效。请换一条公开内容再试。"
    if reason_code == "source_access_restricted":
        return "这条内容当前受访问限制，暂时无法处理。请换一条公开内容再试。"
    if reason_code == "browser_runtime_missing":
        return "项目级浏览器运行时还没有准备好，当前这条内容无法走浏览器会话提取。请先补齐运行时后再试。"
    if reason_code == "browser_session_expired":
        return "当前内容需要有效的项目级浏览器会话后才能继续处理。请刷新会话后再试。"
    if reason_code == "browser_challenge_required":
        return "当前页面触发了平台验证，暂时需要刷新项目级浏览器会话后再试。"
    if reason_code == "browser_timeout":
        return "浏览器会话在规定时间内没有稳定拿到页面结果。请稍后重试，或换一条更稳定的公开内容。"
    if reason_code == "browser_request_failed":
        return "浏览器会话没有稳定拿到可用页面结果。请稍后重试，或换一条公开内容再试。"
    if reason_code == "douyin_fresh_cookies_required":
        return "这条抖音内容当前需要更新浏览器会话后再试。你可以稍后重试，或换一条公开视频。"
    if reason_code == "douyin_direct_metadata_timeout":
        return "抖音公开链路响应较慢，已尝试浏览器会话；请稍后重试或刷新浏览器会话后再试。"
    if reason_code == "xiaohongshu_full_url_required":
        return "这条小红书内容请从浏览器地址栏复制完整网页链接后再试。"
    if reason_code == "xiaohongshu_placeholder_page":
        return "这条小红书内容当前不可访问，或页面已经失效。请换一条公开图文或视频再试。"
    if reason_code == "xiaoyuzhou_audio_missing":
        return "这条小宇宙内容没有拿到可转写音频。请换一条公开节目再试。"
    if reason_code == "source_fetch_failed":
        return "当前网络环境没有稳定连上来源站点，请稍后重试。"
    if reason_code == "extract_failed":
        return "没有稳定拿到页面下的正文内容。请稍后重试，或换一条公开内容。"
    if reason_code == "download_failed":
        return "资源下载没有成功，请稍后重试，或换一条内容再试。"
    if reason_code == "transcribe_failed":
        return "媒体已拿到，但转写没有成功。建议稍后重试，或换一个更清晰的音视频文件。"

    default_message = PUBLIC_ERROR_MESSAGES.get(stage)
    if default_message:
        return default_message

    cleaned = _clean_text(raw_message)
    if cleaned and not re.search(r"[A-Za-z_]+Error|Traceback|Exception|FileNotFoundError", cleaned):
        return cleaned
    return PUBLIC_ERROR_MESSAGES["internal"]


def _enforce_free_duration_limit(source: SourceMetaModel) -> None:
    if not _is_media_content(source.content_type):
        return
    if not source.duration_seconds:
        return
    if source.duration_seconds <= FREE_DURATION_SECONDS:
        return
    raise ExtractionError("limit", PUBLIC_ERROR_MESSAGES["limit"])


def enqueue_capture(capture_id: str) -> None:
    ensure_worker_started()
    capture = repository.get(capture_id)
    if capture is None or capture.status != "queued":
        return
    with worker_lock:
        if capture_id in active_capture_ids or capture_id in queued_capture_ids:
            return
        queued_capture_ids.add(capture_id)
    capture_queue.put(capture_id)


def recover_pending_captures() -> None:
    for capture_id in repository.list_pending_ids():
        capture = repository.get(capture_id)
        if capture is None:
            continue
        if capture.input_type == "file" and capture.storage_path and not Path(capture.storage_path).exists():
            repository.update(
                capture_id,
                status="failed",
                finished_at=now_iso(),
                error_stage="input",
                error_message="排队中的本地文件已经不存在，请重新上传。",
                processing=_update_processing(
                    capture,
                    current_stage="failed",
                    progress_detail="本地文件已缺失，无法恢复任务。",
                    trace=[_make_trace("input", "本地文件已经丢失，无法恢复任务。", level="error")],
                ),
            )
            continue

        repository.update(
            capture_id,
            status="queued",
            error_stage=None,
            error_message=None,
            processing=_update_processing(
                capture,
                current_stage="queued",
                progress_percent=2,
                progress_detail="任务已恢复到等待队列。",
                reset_failure_state=True,
                trace=[_make_trace("queued", "任务已恢复到等待队列。")],
            ),
        )
        write_capture_event(capture_id, "capture_recovered", stage="queued", previous_status=capture.status)
        if RELAY_RUN_MODE == "local":
            enqueue_capture(capture_id)
    for capture in repository.list(limit=5000):
        if capture.status == "done" and capture.asset_preparation_pending:
            _start_asset_preparation(capture.id)


def process_capture(capture_id: str) -> None:
    capture = repository.get(capture_id)
    with worker_lock:
        queued_capture_ids.discard(capture_id)
    if capture is None:
        return
    if capture.status != "queued":
        return

    with worker_lock:
        if capture_id in active_capture_ids:
            return
        active_capture_ids.add(capture_id)
        queued_capture_ids.discard(capture_id)

    artifact_dir = ARTIFACTS_DIR / capture.id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    repository.capture_workspace(capture.id)
    diagnostics = CaptureDiagnostics(capture.id)
    recovery_seconds = _recovery_seconds_from_trace_events(capture)
    diagnostics.event(
        "capture_started",
        stage="queued",
        status=capture.status,
        recovery_seconds=round(recovery_seconds, 3),
        input_type=capture.input_type,
    )

    try:
        def persist_capture(*, processing=None, **fields):
            nonlocal capture
            capture = repository.update(capture.id, processing=processing, **fields) or capture
            return capture

        def update_live_progress(
            stage: str,
            percent: int,
            detail: str,
            *,
            completed_stage: str | None = None,
            warnings: list[str] | None = None,
            strategies: list[str] | None = None,
            trace: list[TraceEventModel] | None = None,
            reset_failure_state: bool = False,
            failure_reason_code: str | None = None,
            retryable: bool | None = None,
        ):
            current_percent = capture.processing.progress_percent if capture and capture.processing else 0
            bounded_percent = percent if stage == "failed" else max(percent, current_percent)
            return persist_capture(
                processing=_update_processing(
                    capture,
                    current_stage=stage,
                    progress_percent=bounded_percent,
                    progress_detail=detail,
                    completed_stage=completed_stage,
                    warnings=warnings,
                    strategies=strategies,
                    trace=trace,
                    failure_reason_code=failure_reason_code,
                    retryable=retryable,
                    reset_failure_state=reset_failure_state,
                )
            )

        diagnostics.start("resolve", input_type=capture.input_type)
        persist_capture(
            status="processing",
            started_at=capture.started_at or now_iso(),
            processing=_update_processing(
                capture,
                current_stage="resolve",
                progress_percent=5,
                progress_detail="正在识别来源和内容类型。",
                reset_failure_state=True,
                trace=[_make_trace("resolve", "开始识别来源与内容类型。")],
            ),
        )

        if capture.input_type == "url":
            resolved = resolve_url(capture.source.raw_input_text or capture.url or "")
            persist_capture(
                title=capture.title or resolved.normalized_url,
                url=resolved.normalized_url,
                source_platform=resolved.platform,
                content_type=resolved.content_type,
                processing=_update_processing(
                    capture,
                    current_stage="extract",
                    progress_percent=12,
                    progress_detail="正在分发平台提取链路。",
                    completed_stage="resolve",
                    warnings=[resolved.input_warning] if resolved.input_warning else None,
                    strategies=["input_cleanup"],
                    trace=[
                        _make_trace("resolve", "来源识别完成。", provider="resolver", detail=resolved.platform),
                        _make_trace("extract", "开始提取可用内容。"),
                    ],
                ),
            )
            diagnostics.finish("resolve", platform=resolved.platform, content_type=resolved.content_type)
            diagnostics.start("extract", platform=resolved.platform, content_type=resolved.content_type)

            def extraction_progress(percent: int, detail: str) -> None:
                update_live_progress("extract", percent, detail)

            extraction = get_url_adapter(resolved).extract(
                resolved,
                artifact_dir,
                cookie_text="",
                progress_callback=extraction_progress,
            )
            source_warnings = list(extraction.warnings)
            if resolved.input_warning:
                source_warnings.insert(0, resolved.input_warning)
            source = SourceMetaModel(
                platform=extraction.platform,
                content_type=extraction.content_type,
                canonical_url=extraction.canonical_url or resolved.normalized_url,
                source_item_id=extraction.source_item_id or capture.source.source_item_id,
                raw_input_text=resolved.cleaned_input,
                detected_urls=resolved.detected_urls,
                selection_reason=resolved.selection_reason,
                author=extraction.author,
                published_at=extraction.published_at,
                language=extraction.language,
                thumbnail_url=extraction.thumbnail_url,
                duration_seconds=extraction.duration_seconds,
                description=extraction.description,
                image_urls=extraction.image_urls,
                live_photo_video_urls=extraction.live_photo_video_urls,
                strategy=list(extraction.strategy),
                warnings=source_warnings,
                extractor_used=extraction.extractor_used,
                fallback_used=extraction.fallback_used,
                fallback_chain=list(extraction.fallback_chain),
            )
            diagnostics.finish(
                "extract",
                provider=extraction.extractor_used,
                strategy=list(extraction.strategy),
                fallback_chain=list(extraction.fallback_chain),
                needs_transcription=extraction.needs_transcription,
            )
        else:
            source_platform, content_type = resolve_file_type(capture.file_name or "")
            persist_capture(
                source_platform=source_platform,
                content_type=content_type,
                processing=_update_processing(
                    capture,
                    current_stage="extract",
                    progress_percent=12,
                    progress_detail="正在准备本地文件内容。",
                    completed_stage="resolve",
                    trace=[
                        _make_trace("resolve", "已识别本地文件类型。", provider="file_resolver", detail=content_type),
                        _make_trace("extract", "开始准备本地文件内容。"),
                    ],
                ),
            )
            diagnostics.finish("resolve", platform=source_platform, content_type=content_type)
            diagnostics.start("extract", platform=source_platform, content_type=content_type)

            extraction = LocalFileAdapter(content_type).extract_file(capture.storage_path or "")
            source = SourceMetaModel(
                platform=extraction.platform,
                content_type=extraction.content_type,
                canonical_url=None,
                source_item_id=extraction.source_item_id or capture.file_name,
                author=None,
                published_at=None,
                language=extraction.language,
                thumbnail_url=None,
                duration_seconds=extraction.duration_seconds,
                description=extraction.description,
                image_urls=extraction.image_urls,
                live_photo_video_urls=extraction.live_photo_video_urls,
                strategy=list(extraction.strategy),
                warnings=list(extraction.warnings),
                extractor_used=extraction.extractor_used,
                fallback_used=extraction.fallback_used,
                fallback_chain=list(extraction.fallback_chain),
            )
            diagnostics.finish(
                "extract",
                provider=extraction.extractor_used,
                strategy=list(extraction.strategy),
                needs_transcription=extraction.needs_transcription,
            )

        _enforce_free_duration_limit(source)

        next_stage = "transcribe" if extraction.needs_transcription else "compose"
        next_detail = "未命中字幕，正在进入转写。" if extraction.needs_transcription else _fast_path_detail(extraction)
        next_percent = 60 if extraction.needs_transcription else 85
        resolved_title = _result_title(capture=capture, source=source, extraction=extraction)
        persist_capture(
            title=resolved_title or capture.title,
            source_platform=source.platform,
            content_type=source.content_type,
            source=source,
            processing=_update_processing(
                capture,
                current_stage=next_stage,
                progress_percent=next_percent,
                progress_detail=next_detail,
                completed_stage="extract",
                warnings=source.warnings,
                strategies=source.strategy,
                trace=[
                    *_provider_trace_events(extraction=extraction),
                    _make_trace("extract", "内容提取完成。", provider="source_provider", detail=" -> ".join(source.strategy)),
                    _make_trace(next_stage, "开始整理最终结果。" if not extraction.needs_transcription else "开始进入转写阶段。"),
                ],
            ),
        )

        transcription: TranscriptionResult | None = None
        if extraction.needs_transcription:
            if not extraction.media_file_path:
                raise ExtractionError("transcribe", "媒体内容缺少可转写文件。")

            diagnostics.start("transcribe", media_file_path=extraction.media_file_path)

            def transcription_progress(progress_ratio: float, detail: str) -> None:
                bounded_ratio = min(max(progress_ratio, 0.0), 1.0)
                percent = 60 + int(round(32 * bounded_ratio))
                update_live_progress("transcribe", percent, detail)

            transcription = transcription_provider.transcribe(
                extraction.media_file_path,
                capture_id=capture.id,
                output_folder=artifact_dir,
                output_stem="raw_transcript",
                source_url=capture.url,
                progress_callback=transcription_progress,
            )
            diagnostics.finish(
                "transcribe",
                provider=transcription.provider,
                segments=transcription.segment_count,
            )
            update_live_progress(
                "compose",
                92,
                "转写完成，正在整理最终结果。",
                completed_stage="transcribe",
                trace=[
                    _make_trace(
                        "transcribe",
                        "转写完成。",
                        provider=transcription.provider,
                        detail=f"segments={transcription.segment_count}",
                    ),
                    _make_trace("compose", "开始组装文字结果。"),
                ],
            )

        diagnostics.start("compose")
        result = _merge_result(capture, source, extraction, transcription)
        _ensure_final_result(source, result)
        persist_capture(
            source=source,
            processing=_update_processing(
                capture,
                current_stage="compose",
                progress_percent=96,
                progress_detail="正文已整理完成，正在生成交付文件。",
                reset_failure_state=True,
                completed_stage="compose",
                trace=[_make_trace("compose", "结果组装完成。")],
            ),
            result=result,
        )
        diagnostics.finish("compose", result_type=result.primary_result_type, text_source=result.text_source)

        diagnostics.start("artifact")
        prepare_images_async = _should_prepare_images_async(source)
        preview_image_limit = 4 if prepare_images_async else 0
        persisted_result = _persist_result_artifacts(
            capture,
            capture.result or result,
            media_file_path=extraction.media_file_path,
            include_images=not prepare_images_async,
            image_preview_limit=preview_image_limit,
            progress_callback=(
                None
                if prepare_images_async
                else lambda percent, detail: update_live_progress("compose", percent, detail)
            ),
        )
        diagnostics.finish(
            "artifact",
            artifact_count=len(persisted_result.artifacts),
            asset_preparation_pending=prepare_images_async,
        )
        artifact_traces = _video_artifact_delivery_traces(persisted_result, extraction.media_file_path)
        for event in artifact_traces:
            logger.info("Capture video delivery profile: %s | %s", capture.id, event.detail or "")
        done_message = (
            "正文和首批图片已可查看，下载文件仍在后台准备。"
            if prepare_images_async
            else "处理完成，结果已可查看和下载。"
        )
        persist_capture(
            status="done",
            finished_at=now_iso(),
            source=source,
            asset_preparation_pending=prepare_images_async,
            processing=_update_processing(
                capture,
                current_stage="completed",
                progress_percent=100,
                progress_detail=done_message,
                reset_failure_state=True,
                trace=[*artifact_traces, _make_trace("done", done_message)],
            ),
            result=persisted_result,
        )
        if prepare_images_async:
            _start_asset_preparation(capture.id)
        diagnostics.finish_capture(
            status="done",
            recovery_seconds=recovery_seconds,
            artifact_count=len(persisted_result.artifacts),
            asset_preparation_pending=prepare_images_async,
        )
        logger.info("Capture stage durations: %s | %s", capture.id, diagnostics.summary_text(recovery_seconds=recovery_seconds))
        logger.info("Capture completed: %s", capture.id)

    except ExtractionError as exc:
        logger.warning("Capture failed: %s | %s | %s", capture.id, exc.stage, exc.message)
        diagnostics.fail(exc.stage, reason_code=exc.reason_code, message=exc.message, retryable=exc.retryable)
        diagnostics.finish_capture(
            status="failed",
            recovery_seconds=recovery_seconds,
            error_stage=exc.stage,
            reason_code=exc.reason_code,
            retryable=exc.retryable,
        )
        logger.info("Capture stage durations: %s | %s", capture.id, diagnostics.summary_text(recovery_seconds=recovery_seconds))
        repository.update(
            capture.id,
            status="failed",
            finished_at=now_iso(),
            error_stage=exc.stage,
            error_message=_public_error_message_with_reason(exc.stage, exc.reason_code, exc.message),
            processing=_update_processing(
                capture,
                current_stage="failed",
                progress_percent=0,
                progress_detail=_public_error_message_with_reason(exc.stage, exc.reason_code, exc.message),
                warnings=exc.warnings,
                trace=[
                    *_provider_trace_events(exc=exc),
                    _make_trace(
                        exc.stage,
                        _public_error_message_with_reason(exc.stage, exc.reason_code, exc.message),
                        level="error",
                        detail=exc.message,
                    ),
                ],
                failure_reason_code=exc.reason_code,
                retryable=exc.retryable,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Capture failed unexpectedly: %s", capture.id)
        diagnostics.fail("internal", message=str(exc), reason_code="internal_error", retryable=True)
        diagnostics.finish_capture(
            status="failed",
            recovery_seconds=recovery_seconds,
            error_stage="internal",
            reason_code="internal_error",
            retryable=True,
        )
        logger.info("Capture stage durations: %s | %s", capture.id, diagnostics.summary_text(recovery_seconds=recovery_seconds))
        repository.update(
            capture.id,
            status="failed",
            finished_at=now_iso(),
            error_stage="internal",
            error_message=PUBLIC_ERROR_MESSAGES["internal"],
            processing=_update_processing(
                capture,
                current_stage="failed",
                progress_percent=0,
                progress_detail=PUBLIC_ERROR_MESSAGES["internal"],
                trace=[_make_trace("internal", PUBLIC_ERROR_MESSAGES["internal"], level="error", detail=str(exc))],
                failure_reason_code="internal_error",
                retryable=True,
            ),
        )


    finally:
        with worker_lock:
            active_capture_ids.discard(capture_id)


def process_pending_capture_once() -> bool:
    for capture_id in repository.list_pending_ids():
        capture = repository.get(capture_id)
        if capture is None:
            continue
        if capture.status == "queued":
            process_capture(capture_id)
            return True
    return False


def run_worker_polling_loop(poll_interval_seconds: float = 1.5) -> None:
    logger.info("Capture worker polling loop started.")
    while True:
        processed = process_pending_capture_once()
        if not processed:
            time.sleep(poll_interval_seconds)


def _local_queue_worker_loop() -> None:
    logger.info("Capture local worker loop started.")
    while True:
        capture_id = capture_queue.get()
        try:
            process_capture(capture_id)
        finally:
            capture_queue.task_done()


def ensure_worker_started() -> None:
    global worker_started, worker_thread
    if RELAY_RUN_MODE != "local" or not RELAY_AUTO_START_WORKER:
        logger.info(
            "Capture API started without in-process worker. run_mode=%s auto_start=%s",
            RELAY_RUN_MODE,
            RELAY_AUTO_START_WORKER,
        )
        return
    with worker_lock:
        if worker_thread is not None and worker_thread.is_alive():
            worker_started = True
            return
        thread = threading.Thread(target=_local_queue_worker_loop, daemon=True)
        thread.start()
        worker_thread = thread
        worker_started = True
        for capture_id in repository.list_pending_ids():
            if capture_id in active_capture_ids or capture_id in queued_capture_ids:
                continue
            queued_capture_ids.add(capture_id)
            capture_queue.put(capture_id)

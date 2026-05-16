from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlparse

import urllib.parse

import av
import httpx
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.resolver import ResolvedSource
from app.settings import BROWSER_PROFILE_DIR, CN_PLATFORM_PROXY, CN_PLATFORM_RELAY_URL, DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT, DEPLOYMENT_MODE, FFMPEG_PATH, read_cookie_payload


ProgressCallback = Callable[[int, str], None]
DOUYIN_DIRECT_METADATA_TIMEOUT_SECONDS = 45.0


@dataclass(slots=True)
class ProviderTraceEntry:
    stage: str
    level: str
    provider: str | None
    message: str
    detail: str | None = None


DEFAULT_REASON_CODES = {
    "input": "input_invalid",
    "resolve": "resolve_failed",
    "extract": "extract_failed",
    "download": "download_failed",
    "transcribe": "transcribe_failed",
    "limit": "limit_exceeded",
}

DEFAULT_RETRYABLE_BY_STAGE = {
    "input": False,
    "resolve": False,
    "extract": False,
    "download": True,
    "transcribe": True,
    "limit": False,
}


class ExtractionError(RuntimeError):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        warnings: list[str] | None = None,
        reason_code: str | None = None,
        retryable: bool | None = None,
        provider_traces: list[ProviderTraceEntry] | None = None,
    ):
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.warnings = warnings or []
        self.reason_code = reason_code or DEFAULT_REASON_CODES.get(stage, "unknown_error")
        self.retryable = retryable if retryable is not None else DEFAULT_RETRYABLE_BY_STAGE.get(stage, False)
        self.provider_traces = provider_traces or []


@dataclass(slots=True)
class ExtractionOutcome:
    platform: str
    content_type: str
    title: str
    canonical_url: str | None = None
    source_item_id: str | None = None
    description: str = ""
    author: str | None = None
    published_at: str | None = None
    language: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    image_urls: list[str] = field(default_factory=list)
    live_photo_video_urls: list[str] = field(default_factory=list)
    strategy: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    primary_text: str = ""
    article_text: str = ""
    notes_text: str = ""
    subtitle_text: str = ""
    subtitle_timeline_text: str = ""
    subtitle_source: str = "none"
    selected_language: str | None = None
    ocr_text: str = ""
    media_file_path: str | None = None
    needs_transcription: bool = False
    extractor_used: str = ""
    fallback_used: bool = False
    fallback_chain: list[str] = field(default_factory=list)
    provider_traces: list[ProviderTraceEntry] = field(default_factory=list)


VIDEO_DELIVERY_TARGET_MAX_HEIGHT = 1080


@dataclass(frozen=True, slots=True)
class DownloadSelection:
    path: Path
    format_id: str = ""
    resolution: str = ""
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    recovered: bool = False

    def trace_detail(self) -> str:
        detail = (
            f"format_id={self.format_id or '-'}, "
            f"resolution={self.resolution or '-'}, "
            f"container={self.container or '-'}, "
            f"video_codec={self.video_codec or '-'}, "
            f"audio_codec={self.audio_codec or '-'}"
        )
        if self.recovered:
            return f"{detail}, recovered=yes"
        return detail


LANGUAGE_GROUPS: dict[str, tuple[str, ...]] = {
    "zh": ("zh-hans", "zh-cn", "zh-sg", "zh", "zh-hant", "zh-tw", "zh-hk"),
    "en": ("en", "en-us", "en-gb"),
}


def _normalize_language_code(value: str | None) -> str:
    cleaned = str(value or "").strip().lower().replace("_", "-")
    if not cleaned:
        return ""
    for normalized, variants in LANGUAGE_GROUPS.items():
        if cleaned in variants:
            return normalized
    if "-" in cleaned:
        return cleaned.split("-", 1)[0]
    return cleaned


def _language_variants(base: str) -> tuple[str, ...]:
    normalized = _normalize_language_code(base)
    if not normalized:
        return ()
    return LANGUAGE_GROUPS.get(normalized, (normalized,))


def _detect_text_language(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
    latin_count = len(re.findall(r"[A-Za-z]", cleaned))
    if chinese_count >= 8 and chinese_count >= latin_count:
        return "zh"
    if latin_count >= 24 and latin_count >= chinese_count * 2:
        return "en"
    return ""


def _preferred_source_language(*candidates: str | None) -> str:
    for candidate in candidates:
        normalized = _normalize_language_code(candidate)
        if normalized:
            return normalized
    return ""


def _notes_are_substantial(text: str, *, minimum_length: int = 220, minimum_lines: int = 3) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    if len(cleaned) >= minimum_length:
        return True
    if cleaned.count("\n") + 1 >= minimum_lines:
        return True
    return False


def _social_notes_are_deliverable(text: str) -> bool:
    return _notes_are_substantial(text, minimum_length=140, minimum_lines=2)


class ArticleTextParser(HTMLParser):
    BLOCK_TAGS = {"p", "li", "blockquote", "article", "section", "h1", "h2", "h3", "h4"}
    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._block_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS:
            self._block_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in self.BLOCK_TAGS and self._block_depth > 0:
            self._block_depth -= 1
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._block_depth > 0:
            self.chunks.append(text)

    def extract_text(self) -> str:
        text = "\n".join(chunk.strip() for chunk in "".join(self.chunks).splitlines() if chunk.strip())
        lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and stripped not in lines:
                lines.append(stripped)
        return "\n".join(lines)


class ImageUrlParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "img":
            return
        attr_map = dict(attrs)
        candidate = (
            attr_map.get("data-src")
            or attr_map.get("data-original")
            or attr_map.get("data-lazy-src")
            or attr_map.get("src")
            or ""
        ).strip()
        if not candidate or candidate.startswith("data:"):
            return
        normalized = urljoin(self.base_url, candidate)
        if normalized not in self.urls:
            self.urls.append(normalized)


class ElementInnerHtmlParser(HTMLParser):
    def __init__(self, target_id: str) -> None:
        super().__init__(convert_charrefs=False)
        self.target_id = target_id
        self._capture_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_map = dict(attrs)
        raw = self.get_starttag_text() or f"<{tag}>"
        if self._capture_depth:
            self._parts.append(raw)
            self._capture_depth += 1
            return
        if attr_map.get("id") == self.target_id:
            self._capture_depth = 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        raw = self.get_starttag_text() or f"<{tag} />"
        if self._capture_depth:
            self._parts.append(raw)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture_depth:
            return
        self._capture_depth -= 1
        if self._capture_depth:
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._capture_depth:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._capture_depth:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._capture_depth:
            self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self._capture_depth:
            self._parts.append(f"<!--{data}-->")

    def extracted_html(self) -> str:
        return "".join(self._parts)


class SilentYtdlpLogger:
    def debug(self, msg: str) -> None:
        return None

    def info(self, msg: str) -> None:
        return None

    def warning(self, msg: str) -> None:
        return None

    def error(self, msg: str) -> None:
        return None


ANSI_ESCAPE_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
ANSI_TEXT_TOKEN_PATTERN = re.compile(r"\[(?:\d{1,3};?)+m")


def _strip_ansi(value: str) -> str:
    cleaned = ANSI_ESCAPE_PATTERN.sub("", str(value or ""))
    cleaned = ANSI_TEXT_TOKEN_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _classify_external_error(message: str, *, platform: str = "", stage: str = "extract") -> tuple[str, bool]:
    cleaned = _strip_ansi(message).lower()

    if "video unavailable" in cleaned:
        return "youtube_video_unavailable", False
    if "no transcripts were found" in cleaned or "transcript is disabled" in cleaned:
        return "youtube_transcript_unavailable", False
    if "fresh cookies" in cleaned:
        return f"{platform or 'source'}_fresh_cookies_required", True
    if "sign in" in cleaned or "login required" in cleaned:
        return f"{platform or 'source'}_login_required", False
    if "private video" in cleaned or "private" in cleaned:
        return f"{platform or 'source'}_private_or_restricted", False
    if "http error 404" in cleaned or "not found" in cleaned:
        return "source_not_found", False
    if "http error 403" in cleaned or "forbidden" in cleaned:
        return "source_access_restricted", False
    if "timed out" in cleaned or "timeout" in cleaned:
        return f"{platform or 'source'}_{stage}_timeout", True
    if "connection" in cleaned or "temporarily unavailable" in cleaned:
        return f"{platform or 'source'}_{stage}_network_failed", True
    if "keyerror('bvid')" in cleaned or "keyerror(\"bvid\")" in cleaned:
        return "bilibili_video_invalid", False
    return DEFAULT_REASON_CODES.get(stage, "unknown_error"), DEFAULT_RETRYABLE_BY_STAGE.get(stage, False)


def _request_headers() -> dict[str, str]:
    return {"User-Agent": DEFAULT_USER_AGENT}


CN_PLATFORMS = {"xiaohongshu", "douyin", "wechat_article", "bilibili"}
RELAY_PLATFORMS = CN_PLATFORMS | {"youtube"}


def _proxy_for_platform(platform: str | None = None) -> str | None:
    """Return the configured proxy URL when the target platform is known to need a mainland-China exit IP."""
    if not CN_PLATFORM_PROXY:
        return None
    if platform and platform in CN_PLATFORMS:
        return CN_PLATFORM_PROXY
    return None


def _platform_needs_relay(platform: str | None = None) -> bool:
    """Whether the platform needs routing through the Cloudflare Worker relay."""
    if not platform or platform not in RELAY_PLATFORMS:
        return False
    if CN_PLATFORM_PROXY:
        return False
    if DEPLOYMENT_MODE == "local":
        return False
    return True


def _relay_fetch(url: str, *, timeout_seconds: float | None = None) -> tuple[str, str]:
    """Fetch a URL through the Cloudflare Worker relay, returning (final_url, text)."""
    request_timeout = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    relay_url = f"{CN_PLATFORM_RELAY_URL}?url={urllib.parse.quote(url, safe='')}"
    try:
        with httpx.Client(
            follow_redirects=False,
            headers=_request_headers(),
            timeout=request_timeout,
            trust_env=False,
        ) as client:
            response = client.get(relay_url)
            response.raise_for_status()
            final_url = response.headers.get("X-Proxied-Url", url)
            return final_url, response.text
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in {404, 410}:
            raise ExtractionError(
                "extract",
                f"无法连接来源站点：{exc}",
                reason_code="source_not_found",
                retryable=False,
            ) from exc
        if status_code in {401, 403}:
            raise ExtractionError(
                "extract",
                f"无法连接来源站点：{exc}",
                reason_code="source_access_restricted",
                retryable=False,
            ) from exc
        raise ExtractionError(
            "extract",
            f"无法连接来源站点：{exc}",
            reason_code="source_fetch_failed",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise ExtractionError(
            "extract",
            f"无法连接来源站点：{exc}",
            reason_code="source_fetch_failed",
            retryable=True,
        ) from exc


def _relay_fetch_json(url: str, *, timeout_seconds: float | None = None) -> dict:
    """Fetch a URL through the Cloudflare Worker relay, returning parsed JSON."""
    request_timeout = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    relay_url = f"{CN_PLATFORM_RELAY_URL}?url={urllib.parse.quote(url, safe='')}"
    try:
        with httpx.Client(
            follow_redirects=False,
            headers=_request_headers(),
            timeout=request_timeout,
            trust_env=False,
        ) as client:
            response = client.get(relay_url)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError):
        return {}


def _httpx_client(*, proxy: str | None = None, **kwargs) -> httpx.Client:
    """Return an httpx.Client, optionally routing through a proxy."""
    if proxy:
        kwargs.setdefault("proxy", proxy)
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("headers", _request_headers())
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
    kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)


def _ffmpeg_location() -> str:
    return str(FFMPEG_PATH)


def _looks_like_video_asset(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}


def _looks_like_audio_asset(path: Path) -> bool:
    return path.suffix.lower() in {".m4a", ".mp3", ".aac", ".wav", ".ogg", ".opus"}


def _merge_media_streams(video_path: Path, audio_path: Path, output_path: Path) -> Path | None:
    command = [
        _ffmpeg_location(),
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        return None
    return output_path


def _recover_downloaded_media(capture_dir: Path, *, content_type: str) -> Path | None:
    candidates = [
        item
        for item in capture_dir.iterdir()
        if item.is_file()
        and not item.name.endswith(".part")
        and not item.name.startswith("subtitle_source")
        and not item.name.startswith("raw_transcript")
        and item.name != "capture.txt"
    ]
    if not candidates:
        return None

    video_files = sorted((item for item in candidates if _looks_like_video_asset(item)), key=lambda item: item.stat().st_size, reverse=True)
    if content_type != "video":
        audio_files = sorted((item for item in candidates if _looks_like_audio_asset(item)), key=lambda item: item.stat().st_size, reverse=True)
        return audio_files[0] if audio_files else None

    audio_files = sorted((item for item in candidates if _looks_like_audio_asset(item)), key=lambda item: item.stat().st_size, reverse=True)
    merged_output = capture_dir / "source_media.mp4"
    if merged_output.exists():
        return merged_output
    if video_files and audio_files:
        merged = _merge_media_streams(video_files[0], audio_files[0], merged_output)
        if merged is not None:
            return merged
    return video_files[0] if video_files else None


def _requests_session_without_env() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(_request_headers())
    return session


def _fetch_text(url: str, *, timeout_seconds: float | None = None, platform: str | None = None) -> tuple[str, str]:
    if _platform_needs_relay(platform):
        return _relay_fetch(url, timeout_seconds=timeout_seconds)
    request_timeout = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    try:
        with _httpx_client(proxy=_proxy_for_platform(platform), timeout=request_timeout) as client:
            response = client.get(url)
            response.raise_for_status()
            return str(response.url), response.text
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in {404, 410}:
            raise ExtractionError(
                "extract",
                f"无法连接来源站点：{exc}",
                reason_code="source_not_found",
                retryable=False,
            ) from exc
        if status_code in {401, 403}:
            raise ExtractionError(
                "extract",
                f"无法连接来源站点：{exc}",
                reason_code="source_access_restricted",
                retryable=False,
            ) from exc
        raise ExtractionError(
            "extract",
            f"无法连接来源站点：{exc}",
            reason_code="source_fetch_failed",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise ExtractionError(
            "extract",
            f"无法连接来源站点：{exc}",
            reason_code="source_fetch_failed",
            retryable=True,
        ) from exc


def _download_file(url: str, target_path: Path, *, platform: str | None = None) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: httpx.HTTPError | None = None
    for _ in range(3):
        try:
            target_path.unlink(missing_ok=True)
            with _httpx_client(proxy=_proxy_for_platform(platform)) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(target_path, "wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
            return target_path
        except httpx.HTTPError as exc:
            last_error = exc

    if last_error is not None:
        target_path.unlink(missing_ok=True)
        raise ExtractionError(
            "download",
            f"下载资源失败：{last_error}",
            reason_code="resource_download_failed",
            retryable=True,
        ) from last_error
    return target_path


def _extract_meta_tags(html: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    meta_pattern = re.compile(
        r"<meta[^>]+(?:property|name)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']*)[\"'][^>]*>",
        re.IGNORECASE,
    )
    for match in meta_pattern.finditer(html):
        key = match.group(1).strip().lower()
        value = unescape(match.group(2).strip())
        tags[key] = value

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        tags["title"] = unescape(" ".join(title_match.group(1).split()))
    return tags


def _extract_json_script(
    html: str,
    *,
    script_id: str | None = None,
    script_name: str | None = None,
    script_type: str | None = None,
) -> dict | None:
    if not any((script_id, script_name, script_type)):
        return None

    attributes: list[str] = []
    if script_id:
        attributes.append(rf'(?=[^>]*id="{re.escape(script_id)}")')
    if script_name:
        attributes.append(rf'(?=[^>]*name="{re.escape(script_name)}")')
    if script_type:
        attributes.append(rf'(?=[^>]*type="{re.escape(script_type)}")')
    pattern = re.compile(
        rf"<script\b{''.join(attributes)}[^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return None

    raw = match.group(1).strip()
    if not raw:
        return None
    try:
        payload = json.loads(unescape(raw))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_article_text(html: str) -> str:
    parser = ArticleTextParser()
    parser.feed(html)
    return parser.extract_text()


def _normalize_rich_text(value: str | None) -> str:
    html_text = _extract_article_text(value or "")
    if html_text:
        return html_text
    return "\n".join(line.strip() for line in unescape(str(value or "")).splitlines() if line.strip())


def _extract_xiaoyuzhou_episode_payload(html: str) -> dict | None:
    payload = _extract_json_script(html, script_id="__NEXT_DATA__", script_type="application/json")
    if not payload:
        return None

    props = payload.get("props")
    if not isinstance(props, dict):
        return None
    page_props = props.get("pageProps")
    if not isinstance(page_props, dict):
        return None
    episode = page_props.get("episode")
    return episode if isinstance(episode, dict) else None


def _extract_xiaoyuzhou_episode_fields(html: str, meta: dict[str, str]) -> dict[str, str]:
    episode = _extract_xiaoyuzhou_episode_payload(html) or {}
    podcast = episode.get("podcast") if isinstance(episode.get("podcast"), dict) else {}
    schema = _extract_json_script(
        html,
        script_name="schema:podcast-show",
        script_type="application/ld+json",
    ) or {}

    shownotes = _normalize_rich_text(episode.get("shownotes") or "")
    episode_description = _normalize_rich_text(episode.get("description") or "")
    schema_description = _normalize_rich_text(schema.get("description") or "")
    body_text = shownotes or episode_description or schema_description

    title = (
        str(episode.get("title") or "").strip()
        or str(schema.get("name") or "").strip()
        or meta.get("og:title")
        or meta.get("title")
        or "小宇宙内容"
    )
    thumbnail_url = (
        str(episode.get("image") or "").strip()
        or meta.get("og:image")
        or ""
    )

    return {
        "title": title,
        "body_text": body_text,
        "body_source": "shownotes" if shownotes else ("description" if episode_description else ("jsonld_description" if schema_description else "none")),
        "podcast_description": _normalize_rich_text(podcast.get("description") or ""),
        "thumbnail_url": thumbnail_url,
    }


def _extract_image_urls(html: str, base_url: str) -> list[str]:
    parser = ImageUrlParser(base_url)
    parser.feed(html)
    return parser.urls[:24]


def _extract_element_inner_html(html: str, *, target_id: str) -> str:
    parser = ElementInnerHtmlParser(target_id)
    parser.feed(html)
    return parser.extracted_html()


def _extract_wechat_content_html(html: str) -> str:
    extracted = _extract_element_inner_html(html, target_id="js_content")
    return extracted or html


def _normalize_wechat_text(value: str) -> str:
    text = (value or "").replace("\\x0d", "")
    text = text.replace("\\x0a", "\n").replace("\\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_wechat_article_text(html: str) -> str:
    return _normalize_wechat_text(_extract_article_text(_extract_wechat_content_html(html)))


def _extract_wechat_image_urls(html: str, base_url: str) -> list[str]:
    target_html = _extract_wechat_content_html(html)
    urls: list[str] = []
    seen: set[str] = set()
    image_pattern = re.compile(r"<img\b[^>]*>", re.IGNORECASE)

    def style_px(style_value: str, property_name: str) -> float | None:
        if not style_value:
            return None
        match = re.search(rf"{property_name}\s*:\s*([0-9.]+)px", style_value, re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def attr(tag: str, name: str) -> str:
        match = re.search(rf'{name}=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        return unescape(match.group(1).strip()) if match else ""

    for tag_match in image_pattern.finditer(target_html):
        tag = tag_match.group(0)
        candidate = (
            attr(tag, "data-src")
            or attr(tag, "data-original")
            or attr(tag, "data-lazy-src")
            or attr(tag, "src")
        )
        if not candidate:
            continue

        normalized = urljoin(base_url, candidate)
        lowered = normalized.lower()
        marker = " ".join(
            filter(
                None,
                [
                    attr(tag, "class"),
                    attr(tag, "id"),
                    attr(tag, "alt"),
                    attr(tag, "title"),
                ],
            )
        ).lower()
        context_chunk = target_html[max(0, tag_match.start() - 320) : min(len(target_html), tag_match.end() + 1200)]
        context_text = re.sub(r"<[^>]+>", " ", context_chunk)
        context_text = re.sub(r"\s+", " ", unescape(context_text)).strip().lower()
        style_value = attr(tag, "style")
        ratio = None
        try:
            ratio = float(attr(tag, "data-ratio")) if attr(tag, "data-ratio") else None
        except ValueError:
            ratio = None
        display_width = style_px(style_value, "width")
        display_height = style_px(style_value, "height")

        if not any(token in lowered for token in ("mmbiz", "wx_fmt=", "tp=webp")):
            continue
        if any(token in lowered for token in ("mmbiz_gif", "wx_fmt=gif")) or lowered.endswith(".gif"):
            continue
        if lowered.endswith(".svg") or "wx_fmt=svg" in lowered:
            continue
        if any(
            token in marker
            for token in ("icon", "logo", "arrow", "share", "qrcode", "qr", "avatar", "badge", "guide", "toolbar")
        ):
            continue
        if any(
            token in lowered
            for token in (
                "icon",
                "logo",
                "arrow",
                "share",
                "qrcode",
                "avatar",
                "badge",
                "backtop",
                "menu",
                "button",
                "toolbar",
                "guide",
                "float",
                "icon_arrow",
                "share_pic",
            )
        ):
            continue

        width = attr(tag, "data-w") or attr(tag, "width")
        height = attr(tag, "data-h") or attr(tag, "height")
        try:
            if width and height and int(float(width)) < 160 and int(float(height)) < 160:
                continue
        except ValueError:
            pass
        if any(
            token in context_text
            for token in (
                "点蓝色字关注",
                "长按左方二维码",
                "长按二维码",
                "更多新闻",
                "扫码关注",
                "微信扫码",
                "↓↓↓",
                "点击查看",
                "点击图片",
                "点击进入",
                "点击打开",
                "点击放大",
                "返回顶部",
                "立即预约",
                "滑动查看",
                "上滑",
                "下滑",
            )
        ):
            continue
        if "二维码" in context_text and any(token in context_text for token in ("关注", "长按", "扫码")):
            continue
        if display_width and display_width <= 240 and ratio and 0.8 <= ratio <= 1.25 and not context_text:
            continue
        if display_width and display_width <= 240 and any(token in marker for token in ("placeholder", "wx_img_placeholder")):
            if "关注" in context_text or "二维码" in context_text or not context_text:
                continue
        if display_width and display_width >= 520 and ratio and ratio >= 2.1:
            if any(token in context_text for token in ("更多新闻", "↓↓↓", "关注", "二维码", "扫码")):
                continue
        if display_height and display_height <= 220 and display_width and display_width <= 220 and "二维码" in context_text:
            continue
        if ratio and 0.82 <= ratio <= 1.18 and display_width and display_width <= 320:
            if any(
                token in context_text
                for token in ("点击", "打开", "进入", "预约", "分享", "返回顶部", "长按", "二维码", "扫码", "滑动")
            ):
                continue

        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    return urls


def _probe_media_duration(media_path: str | None) -> float | None:
    if not media_path:
        return None
    path = Path(media_path)
    if not path.exists():
        return None
    try:
        with av.open(str(path)) as container:
            if container.duration:
                return float(container.duration / av.time_base)
            stream_durations = []
            for stream in container.streams:
                if stream.duration and stream.time_base:
                    stream_durations.append(float(stream.duration * stream.time_base))
            return max(stream_durations) if stream_durations else None
    except Exception:
        return None


def _format_subtitle_timestamp(milliseconds: int) -> str:
    total_seconds = max(milliseconds, 0) // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _normalize_subtitle_text(value: str) -> str:
    lines = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"\s+", " ", unescape(line)).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _parse_json3_subtitle(raw_text: str) -> tuple[str, str]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return "", ""

    body_lines: list[str] = []
    timeline_lines: list[str] = []
    for event in payload.get("events") or []:
        parts: list[str] = []
        for segment in event.get("segs") or []:
            text = str(segment.get("utf8") or "")
            if text:
                parts.append(text)
        cleaned = _normalize_subtitle_text("".join(parts))
        if not cleaned:
            continue
        body_lines.append(cleaned)
        start_ms = event.get("tStartMs")
        if isinstance(start_ms, int):
            timeline_lines.append(f"{_format_subtitle_timestamp(start_ms)} {cleaned}")

    return "\n".join(body_lines), "\n".join(timeline_lines)


def _parse_web_subtitle(raw_text: str) -> tuple[str, str]:
    stripped_raw = raw_text.lstrip()
    if stripped_raw.startswith("{"):
        parsed = _parse_json3_subtitle(raw_text)
        if parsed[0]:
            return parsed

    timeline_lines: list[str] = []
    body_lines: list[str] = []

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("WEBVTT") or stripped.isdigit():
            continue
        if "-->" in stripped:
            timeline_lines.append(stripped)
            continue
        cleaned = re.sub(r"<[^>]+>", "", stripped)
        if cleaned:
            body_lines.append(cleaned)
            if timeline_lines and not timeline_lines[-1].endswith(cleaned):
                timeline_lines[-1] = f"{timeline_lines[-1]} {cleaned}"

    return "\n".join(body_lines), "\n".join(timeline_lines)


def _cookie_file(cookie_text: str, capture_dir: Path) -> Path | None:
    normalized = read_cookie_payload(cookie_text)
    if not normalized:
        return None
    cookie_path = capture_dir / "cookies.txt"
    cookie_path.write_text(normalized, encoding="utf-8")
    return cookie_path


def _browser_cookie_sources(cookie_file: Path | None, platform: str) -> list[tuple[str, ...] | None]:
    if cookie_file or platform not in {"douyin", "xiaohongshu"}:
        return [None]
    return [
        ("edge", str(BROWSER_PROFILE_DIR)),
        ("chrome", str(BROWSER_PROFILE_DIR)),
        ("chromium", str(BROWSER_PROFILE_DIR)),
        ("edge",),
        ("chrome",),
        ("chromium",),
        None,
    ]


def _source_media_language(info: dict) -> str:
    return _preferred_source_language(
        info.get("language"),
        info.get("original_language"),
        info.get("release_language"),
    )


def _track_source(pool_name: str, *, translated: bool) -> str:
    if translated:
        return "translated"
    return "manual" if pool_name == "subtitles" else "auto"


def _track_priority(*, source_language: str, normalized_language: str, pool_name: str, translated: bool) -> tuple[int, int, int, str]:
    source_match = bool(source_language and normalized_language == source_language)
    manual_track = pool_name == "subtitles"
    if source_match and manual_track and not translated:
        rank = 0
    elif source_match and not translated:
        rank = 1
    elif manual_track and not translated:
        rank = 2
    elif not translated:
        rank = 3
    elif source_match and manual_track:
        rank = 4
    elif source_match:
        rank = 5
    elif manual_track:
        rank = 6
    else:
        rank = 7
    return (rank, 0 if source_match else 1, 0 if manual_track else 1, normalized_language)


def _select_subtitle_track(info: dict) -> tuple[dict | None, str, str]:
    source_language = _source_media_language(info)
    all_tracks = []
    for pool_name in ["subtitles", "automatic_captions"]:
        pool = info.get(pool_name) or {}
        for language, entries in pool.items():
            for entry in entries or []:
                normalized_language = _normalize_language_code(language)
                translated = bool(
                    source_language
                    and normalized_language
                    and normalized_language != source_language
                )
                all_tracks.append(
                    (
                        _track_priority(
                            source_language=source_language,
                            normalized_language=normalized_language,
                            pool_name=pool_name,
                            translated=translated,
                        ),
                        {"language": language, "pool_name": pool_name, "translated": translated, **entry},
                        _track_source(pool_name, translated=translated),
                        language,
                    )
                )

    if not all_tracks:
        return None, "none", ""

    all_tracks.sort(key=lambda item: item[0])
    _, track, subtitle_source, selected_language = all_tracks[0]
    return track, subtitle_source, selected_language


def _flatten_ytdlp_info(info: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    if info.get("_type") == "playlist" and info.get("entries"):
        first = next((entry for entry in info["entries"] if entry), None)
        if first:
            warnings.append("输入里包含多个条目，当前只使用第一条可提取内容。")
            return first, warnings
    return info, warnings


def _ytdlp_metadata(url: str, capture_dir: Path, cookie_text: str = "", platform: str = "") -> tuple[dict, list[str]]:
    cookie_file = _cookie_file(cookie_text, capture_dir)
    base_options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "socket_timeout": 12,
        "retries": 1,
        "proxy": "",
        "ffmpeg_location": _ffmpeg_location(),
        "cookiefile": str(cookie_file) if cookie_file else None,
        "http_headers": {"User-Agent": DEFAULT_USER_AGENT},
        "logger": SilentYtdlpLogger(),
    }
    last_exc: Exception | None = None
    for browser_cookie_source in _browser_cookie_sources(cookie_file, platform):
        options = dict(base_options)
        if browser_cookie_source:
            options["cookiesfrombrowser"] = browser_cookie_source
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            return _flatten_ytdlp_info(info)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue

    reason_code, retryable = _classify_external_error(str(last_exc), platform=platform, stage="extract")
    raise ExtractionError(
        "extract",
        f"无法读取来源元信息：{_strip_ansi(str(last_exc))}",
        reason_code=reason_code,
        retryable=retryable,
    ) from last_exc


def _download_subtitle(track: dict, capture_dir: Path) -> tuple[str, str]:
    subtitle_url = track.get("url")
    if not subtitle_url:
        return "", ""
    ext = track.get("ext") or "vtt"
    subtitle_path = capture_dir / f"subtitle_source.{ext}"
    _download_file(subtitle_url, subtitle_path)
    raw_text = subtitle_path.read_text(encoding="utf-8", errors="ignore")
    return _parse_web_subtitle(raw_text)


def _download_format_selector(content_type: str) -> str:
    if content_type != "video":
        return "bestaudio[ext=m4a]/bestaudio/best"
    return (
        f"bestvideo[vcodec^=avc1][ext=mp4][height<={VIDEO_DELIVERY_TARGET_MAX_HEIGHT}]+bestaudio[ext=m4a]/"
        f"bestvideo[ext=mp4][height<={VIDEO_DELIVERY_TARGET_MAX_HEIGHT}]+bestaudio[ext=m4a]/"
        f"bestvideo[vcodec^=avc1][height<={VIDEO_DELIVERY_TARGET_MAX_HEIGHT}]+bestaudio/"
        f"bestvideo*[height<={VIDEO_DELIVERY_TARGET_MAX_HEIGHT}]+bestaudio/"
        "best[ext=mp4]/best"
    )


def _probe_downloaded_media_profile(path: Path) -> tuple[str, str, str, str]:
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


def _selected_download_format_id(info: dict) -> str:
    requested_formats = info.get("requested_formats") or []
    if requested_formats:
        format_ids = [str(item.get("format_id") or "").strip() for item in requested_formats]
        format_ids = [item for item in format_ids if item]
        if format_ids:
            return "+".join(format_ids)
    requested_downloads = info.get("requested_downloads") or []
    if requested_downloads:
        format_ids = [str(item.get("format_id") or "").strip() for item in requested_downloads]
        format_ids = [item for item in format_ids if item]
        if format_ids:
            return "+".join(format_ids)
    return str(info.get("format_id") or "").strip()


def _download_selection(path: Path, info: dict | None = None, *, recovered: bool = False) -> DownloadSelection:
    container_name, video_codec, audio_codec, resolution = _probe_downloaded_media_profile(path)
    fallback_resolution = ""
    if info is not None:
        requested_formats = info.get("requested_formats") or []
        if requested_formats:
            width = requested_formats[0].get("width")
            height = requested_formats[0].get("height")
            if width and height:
                fallback_resolution = f"{width}x{height}"
        if not fallback_resolution:
            fallback_resolution = str(info.get("resolution") or "").strip()
    return DownloadSelection(
        path=path,
        format_id=_selected_download_format_id(info or {}),
        resolution=resolution or fallback_resolution,
        container=container_name,
        video_codec=video_codec,
        audio_codec=audio_codec,
        recovered=recovered,
    )


def _download_media_selection_with_ytdlp(
    url: str,
    capture_dir: Path,
    cookie_text: str = "",
    platform: str = "",
    content_type: str = "",
) -> DownloadSelection:
    cookie_file = _cookie_file(cookie_text, capture_dir)
    output_template = str(capture_dir / "%(id)s.%(ext)s")
    is_video = content_type == "video"
    base_options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 12,
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 1,
        "format": _download_format_selector(content_type),
        "proxy": "",
        "ffmpeg_location": _ffmpeg_location(),
        "outtmpl": output_template,
        "cookiefile": str(cookie_file) if cookie_file else None,
        "http_headers": {"User-Agent": DEFAULT_USER_AGENT},
        "logger": SilentYtdlpLogger(),
    }
    if is_video:
        base_options["merge_output_format"] = "mp4"
    if platform == "youtube":
        base_options.update(
            {
                "socket_timeout": 20,
                "retries": 3,
                "fragment_retries": 3,
                "extractor_retries": 3,
            }
        )
    last_exc: Exception | None = None
    info = None
    for browser_cookie_source in _browser_cookie_sources(cookie_file, platform):
        options = dict(base_options)
        if browser_cookie_source:
            options["cookiesfrombrowser"] = browser_cookie_source
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Exception as exc:  # noqa: BLE001
            recovered = _recover_downloaded_media(capture_dir, content_type=content_type)
            if recovered is not None:
                return _download_selection(recovered, recovered=True)
            last_exc = exc
            continue

    if info is None:
        recovered = _recover_downloaded_media(capture_dir, content_type=content_type)
        if recovered is not None:
            return _download_selection(recovered, recovered=True)
        raise DownloadError(_strip_ansi(str(last_exc))) from last_exc

    requested = info.get("requested_downloads") or []
    for item in requested:
        filepath = item.get("filepath")
        if filepath:
            candidate = Path(filepath)
            if candidate.exists():
                return _download_selection(candidate, info)
    recovered = _recover_downloaded_media(capture_dir, content_type=content_type)
    if recovered is not None:
        return _download_selection(recovered, info, recovered=True)
    candidate = Path(ydl.prepare_filename(info))
    if candidate.exists():
        return _download_selection(candidate, info)
    raise DownloadError("downloaded media file is missing")


def _download_media_with_ytdlp(
    url: str,
    capture_dir: Path,
    cookie_text: str = "",
    platform: str = "",
    content_type: str = "",
) -> Path:
    return _download_media_selection_with_ytdlp(
        url,
        capture_dir,
        cookie_text=cookie_text,
        platform=platform,
        content_type=content_type,
    ).path


def _youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.endswith("youtu.be"):
        return parsed.path.strip("/").split("/")[0]
    query = parse_qs(parsed.query or "")
    if query.get("v"):
        return query["v"][0]
    match = re.search(r"/shorts/([^/?#]+)", parsed.path or "", re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _render_fetched_transcript(items: list[dict]) -> tuple[str, str]:
    lines: list[str] = []
    timeline_lines: list[str] = []
    for item in items:
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        lines.append(text)
        start = float(item.get("start") or 0.0)
        duration = float(item.get("duration") or 0.0)
        end = start + duration
        timeline_lines.append(f"{start:0.2f}s -> {end:0.2f}s {text}")
    return "\n".join(lines), "\n".join(timeline_lines)


def _select_youtube_transcript(transcript_list, *, preferred_language: str = ""):
    candidates = list(transcript_list)
    if not candidates:
        return None

    indexed_candidates = list(enumerate(candidates))

    def sort_key(item) -> tuple[int, int]:
        index, transcript = item
        normalized_language = _normalize_language_code(getattr(transcript, "language_code", ""))
        preferred_match = bool(preferred_language and normalized_language == preferred_language)
        generated = bool(getattr(transcript, "is_generated", False))
        if preferred_match and not generated:
            rank = 0
        elif preferred_match:
            rank = 1
        elif not generated:
            rank = 2
        else:
            rank = 3
        return (rank, index)

    indexed_candidates.sort(key=sort_key)
    return indexed_candidates[0][1]


def extract_youtube_transcript(
    resolved: ResolvedSource,
    *,
    progress_callback: ProgressCallback | None = None,
) -> ExtractionOutcome:
    video_id = _youtube_video_id(resolved.normalized_url)
    if not video_id:
        raise ExtractionError("resolve", "YouTube 链接可访问，但没有解析出视频 ID。")

    final_url, html = _fetch_text(resolved.normalized_url, platform="youtube")
    meta = _extract_meta_tags(html)
    title = meta.get("og:title") or meta.get("title") or f"YouTube {video_id}"
    description = meta.get("og:description") or meta.get("description") or ""
    preferred_language = ""

    subtitle_text = ""
    subtitle_timeline = ""
    transcript = None
    needs_relay = _platform_needs_relay("youtube")

    # Try direct transcript API first (fast path for local/non-blocked networks)
    if not needs_relay:
        session = _requests_session_without_env()
        try:
            if progress_callback is not None:
                progress_callback(24, "正在检查可用字幕轨。")
            api = YouTubeTranscriptApi(http_client=session)
            transcript_list = api.list(video_id)
            transcript = _select_youtube_transcript(transcript_list, preferred_language=preferred_language)
            if transcript is not None:
                fetched = transcript.fetch()
                subtitle_text, subtitle_timeline = _render_fetched_transcript(fetched.to_raw_data())
                if progress_callback is not None and subtitle_text:
                    progress_callback(46, "已拿到可用字幕，正在整理结果。")
        except Exception:
            pass
        finally:
            session.close()

    # Relay path: extract captions from page HTML or bot-detection fallback
    if not subtitle_text:
        if "unusual traffic" in html.lower() or "captcha" in html.lower():
            raise ExtractionError(
                "extract",
                "YouTube 当前限制了云端服务器的访问，请稍后重试或在本地环境处理。",
                reason_code="youtube_extract_timeout",
                retryable=True,
            )
        else:
            subtitle_text = _extract_youtube_captions_from_page(html)

    if not subtitle_text:
        raise ExtractionError(
            "extract",
            "YouTube 当前没有可用字幕轨。",
            reason_code="youtube_transcript_unavailable",
            retryable=False,
        )

    return ExtractionOutcome(
        platform="youtube",
        content_type="video",
        title=title,
        canonical_url=final_url,
        source_item_id=video_id,
        description=description,
        language=_detect_text_language(subtitle_text[:280]) or "zh",
        thumbnail_url=meta.get("og:image"),
        strategy=["youtube_transcript_api", "subtitle"],
        subtitle_text=subtitle_text,
        subtitle_timeline_text=subtitle_timeline,
        subtitle_source="auto",
        selected_language="",
        primary_text=subtitle_text,
        notes_text="",
        needs_transcription=False,
    )


def _extract_youtube_captions_from_page(html: str) -> str:
    """Extract YouTube captions from embedded ytInitialPlayerResponse JSON."""
    import xml.etree.ElementTree as ET

    match = re.search(r"var\s+ytInitialPlayerResponse\s*=\s*(\{.*?\});\s*</script>", html, re.DOTALL)
    if not match:
        match = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.*?\});", html, re.DOTALL)
    if not match:
        return ""

    try:
        player = json.loads(match.group(1))
    except json.JSONDecodeError:
        return ""

    tracks = (player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}
    caption_tracks = tracks.get("captionTracks") or []

    if not caption_tracks:
        return ""

    # Pick first available track (prefer auto-generated or first non-auto)
    selected = None
    for track in caption_tracks:
        if isinstance(track, dict) and track.get("languageCode"):
            selected = track
            if track.get("vssId", "").startswith("a."):
                break  # prefer auto-captions

    if not selected or not selected.get("baseUrl"):
        return ""

    caption_url = selected["baseUrl"]
    if caption_url.startswith("//"):
        caption_url = "https:" + caption_url

    # Fetch caption XML via relay or direct
    _, caption_xml = _fetch_text(caption_url, platform="youtube")

    try:
        root = ET.fromstring(caption_xml)
    except ET.ParseError:
        return ""

    lines: list[str] = []
    for text_el in root.iter("text"):
        line = "".join(text_el.itertext()).replace("&#39;", "'").replace("&amp;", "&").replace("&quot;", "\"")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _extract_xiaoyuzhou_episode_id(url: str) -> str:
    match = re.search(r"/episode/([^/?#]+)", url or "", re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_xiaohongshu_note_id(url: str) -> str:
    for pattern in (r"/explore/([^/?#]+)", r"/discovery/item/([^/?#]+)", r"/note/([^/?#]+)"):
        match = re.search(pattern, url or "", re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def extract_local_file(file_path: str, *, content_type: str) -> ExtractionOutcome:
    path = Path(file_path)
    return ExtractionOutcome(
        platform="local_file",
        content_type=content_type,
        title=path.name,
        source_item_id=path.name,
        duration_seconds=_probe_media_duration(str(path)),
        strategy=["local_file_media"],
        media_file_path=str(path),
        needs_transcription=True,
    )


def extract_xiaoyuzhou(resolved: ResolvedSource, capture_dir: Path) -> ExtractionOutcome:
    final_url, html = _fetch_text(resolved.normalized_url)
    meta = _extract_meta_tags(html)
    episode_fields = _extract_xiaoyuzhou_episode_fields(html, meta)
    title = episode_fields["title"]
    thumbnail_url = episode_fields["thumbnail_url"] or meta.get("og:image") or ""

    audio_url = meta.get("og:audio")
    if not audio_url:
        candidates = [
            r'"audio"\s*:\s*"(https?://[^\"]+)"',
            r'"audioUrl"\s*:\s*"(https?://[^\"]+)"',
            r'https?://[^"\'\s>]+\.mp3[^"\'\s>]*',
        ]
        for pattern in candidates:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                audio_url = match.group(1) if match.lastindex else match.group(0)
                break

    if not audio_url:
        raise ExtractionError(
            "extract",
            "无法从小宇宙页面提取 episode 音频地址。",
            reason_code="xiaoyuzhou_audio_missing",
            retryable=False,
        )

    media_path = capture_dir / "source_audio.mp3"
    _download_file(audio_url, media_path)

    return ExtractionOutcome(
        platform="xiaoyuzhou",
        content_type="audio",
        title=title,
        canonical_url=final_url,
        source_item_id=_extract_xiaoyuzhou_episode_id(final_url),
        description="",
        thumbnail_url=thumbnail_url or None,
        language="zh",
        duration_seconds=_probe_media_duration(str(media_path)),
        strategy=["episode_audio"],
        notes_text="",
        media_file_path=str(media_path),
        needs_transcription=True,
    )


def extract_generic_web(resolved: ResolvedSource) -> ExtractionOutcome:
    final_url, html = _fetch_text(resolved.normalized_url, platform=resolved.platform)
    meta = _extract_meta_tags(html)
    if resolved.platform == "wechat_article":
        article_text = _extract_wechat_article_text(html)
        image_urls = _extract_wechat_image_urls(html, final_url)
        content_type = "image_article" if image_urls else "article"
    else:
        article_text = _extract_article_text(html)
        image_urls = _extract_image_urls(html, final_url)
        content_type = resolved.content_type or "webpage"
    title = meta.get("og:title") or meta.get("title") or final_url
    description = meta.get("og:description") or meta.get("description") or ""
    if resolved.platform == "wechat_article":
        title = _normalize_wechat_text(title)
        description = _normalize_wechat_text(description)
    primary_text = article_text if len(article_text) >= len(description) else description
    if not primary_text and not image_urls:
        raise ExtractionError("extract", "网页存在，但没有提取到可用正文。")

    if resolved.platform != "wechat_article" and meta.get("og:image") and meta["og:image"] not in image_urls:
        image_urls.insert(0, meta["og:image"])

    return ExtractionOutcome(
        platform=resolved.platform,
        content_type=content_type,
        title=title,
        canonical_url=final_url,
        description=description,
        image_urls=image_urls,
        thumbnail_url=meta.get("og:image"),
        strategy=["page_text", "page_images"] if image_urls else ["page_text"],
        article_text=primary_text,
        primary_text=primary_text,
        needs_transcription=False,
    )


def _extract_douyin_aweme_id(url: str, html: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query or "")
    for key in ("modal_id", "aweme_id", "item_id"):
        values = query.get(key) or []
        if values and values[0].isdigit():
            return values[0]

    for pattern in (r"/video/(\d{10,22})", r"/note/(\d{10,22})"):
        match = re.search(pattern, parsed.path or "", re.IGNORECASE)
        if match:
            return match.group(1)

    html_patterns = [
        r'"awemeId"\s*:\s*"(\d{10,22})"',
        r'"aweme_id"\s*:\s*"(\d{10,22})"',
        r'"itemId"\s*:\s*"(\d{10,22})"',
        r'"item_id"\s*:\s*"(\d{10,22})"',
    ]
    for pattern in html_patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)

    return ""


def _fetch_douyin_item(aweme_id: str, *, deadline: float | None = None) -> dict:
    headers = _request_headers()
    headers["Referer"] = "https://www.douyin.com/"
    candidates = [
        f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={aweme_id}",
        f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={aweme_id}&aid=6383&device_platform=webapp",
    ]
    for url in candidates:
        request_timeout = DEFAULT_TIMEOUT_SECONDS
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {}
            request_timeout = min(DEFAULT_TIMEOUT_SECONDS, max(1.0, remaining))
        try:
            if _platform_needs_relay("douyin"):
                payload = _relay_fetch_json(url, timeout_seconds=request_timeout)
            else:
                with _httpx_client(proxy=_proxy_for_platform("douyin"), headers=headers, timeout=request_timeout) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue

        if not isinstance(payload, dict):
            continue
        item_list = payload.get("item_list")
        if isinstance(item_list, list) and item_list:
            return item_list[0]
        detail = payload.get("aweme_detail")
        if isinstance(detail, dict) and detail:
            return detail
        nested = payload.get("itemInfo", {}).get("itemStruct") if isinstance(payload.get("itemInfo"), dict) else None
        if isinstance(nested, dict) and nested:
            return nested
    return {}


def _collect_douyin_media_urls(item: dict) -> list[str]:
    video = item.get("video") or {}
    candidates: list[str] = []

    def append_from(node) -> None:
        if isinstance(node, str):
            if node and node not in candidates:
                candidates.append(node)
            return
        if not isinstance(node, dict):
            return
        for key in ("url", "play_api", "play_url"):
            value = node.get(key)
            if isinstance(value, str) and value and value not in candidates:
                candidates.append(value)
        url_list = node.get("url_list") or []
        for value in url_list:
            if isinstance(value, str) and value and value not in candidates:
                candidates.append(value)
        uri = node.get("uri")
        if isinstance(uri, str) and uri:
            play_url = f"https://www.douyin.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0"
            if play_url not in candidates:
                candidates.append(play_url)

    append_from(video.get("play_addr"))
    append_from(video.get("download_addr"))
    append_from(video.get("play_addr_h264"))
    append_from(video.get("play_addr_lowbr"))
    append_from(video.get("play_api"))

    for item_node in video.get("bit_rate") or []:
        append_from(item_node.get("play_addr") if isinstance(item_node, dict) else None)

    return candidates


def _douyin_duration_seconds(item: dict) -> float | None:
    video = item.get("video") or {}
    value = video.get("duration") or item.get("duration")
    if not value:
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    # Douyin fields are usually ms.
    if duration > 3600:
        duration /= 1000.0
    return duration


def extract_douyin_direct(
    resolved: ResolvedSource,
    capture_dir: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    metadata_timeout_seconds: float = DOUYIN_DIRECT_METADATA_TIMEOUT_SECONDS,
) -> ExtractionOutcome:
    metadata_deadline = time.monotonic() + metadata_timeout_seconds
    final_url, html = _fetch_text(
        resolved.normalized_url,
        timeout_seconds=min(DEFAULT_TIMEOUT_SECONDS, metadata_timeout_seconds),
        platform="douyin",
    )
    aweme_id = _extract_douyin_aweme_id(final_url, html)
    if not aweme_id:
        raise ExtractionError(
            "extract",
            "抖音链接可访问，但没有解析出视频 ID。",
            reason_code="douyin_video_id_missing",
            retryable=False,
        )

    if time.monotonic() >= metadata_deadline:
        raise ExtractionError(
            "extract",
            "抖音直连公开视频元信息探测超时，已切换浏览器会话。",
            reason_code="douyin_direct_metadata_timeout",
            retryable=True,
        )

    item = _fetch_douyin_item(aweme_id, deadline=metadata_deadline)
    if not item:
        if time.monotonic() >= metadata_deadline:
            raise ExtractionError(
                "extract",
                "抖音直连公开视频元信息探测超时，已切换浏览器会话。",
                reason_code="douyin_direct_metadata_timeout",
                retryable=True,
            )
        raise ExtractionError(
            "extract",
            "抖音链接可访问，但没有拿到公开视频元信息。",
            reason_code="douyin_public_metadata_missing",
            retryable=True,
        )

    if progress_callback is not None:
        progress_callback(26, "正在读取公开视频元信息。")
    media_urls = _collect_douyin_media_urls(item)
    if not media_urls:
        raise ExtractionError(
            "download",
            "抖音元信息已拿到，但没有可下载的媒体地址。",
            reason_code="douyin_media_url_missing",
            retryable=True,
        )

    media_path = capture_dir / "douyin_source.mp4"
    last_message = ""
    if progress_callback is not None:
        progress_callback(36, "正在下载原视频文件。")
    for media_url in media_urls:
        try:
            _download_file(media_url, media_path, platform="douyin")
            break
        except ExtractionError as exc:
            last_message = exc.message
    if not media_path.exists():
        detail = f"：{last_message}" if last_message else ""
        raise ExtractionError(
            "download",
            f"抖音媒体下载失败{detail}",
            reason_code="douyin_media_download_failed",
            retryable=True,
        )
    if progress_callback is not None:
        progress_callback(50, "原视频已就绪，正在准备转写。")

    description = (item.get("desc") or "").strip()
    author = ""
    author_info = item.get("author")
    if isinstance(author_info, dict):
        author = (author_info.get("nickname") or author_info.get("unique_id") or "").strip()

    thumbnail = ""
    video = item.get("video") or {}
    cover = video.get("cover") if isinstance(video, dict) else None
    if isinstance(cover, dict):
        urls = cover.get("url_list") or []
        if urls:
            thumbnail = str(urls[0])

    title = description[:64] if description else f"抖音视频 {aweme_id}"
    return ExtractionOutcome(
        platform="douyin",
        content_type="video",
        title=title,
        canonical_url=(item.get("share_url") or final_url),
        source_item_id=aweme_id,
        description=description,
        author=author or None,
        language="zh",
        thumbnail_url=thumbnail or None,
        duration_seconds=_douyin_duration_seconds(item),
        strategy=["share_resolve", "douyin_item_api", "media_direct"],
        notes_text=description,
        primary_text="",
        media_file_path=str(media_path),
        needs_transcription=True,
    )


def _supplemental_page_text(resolved: ResolvedSource, warnings: list[str]) -> tuple[str, str]:
    try:
        _, html = _fetch_text(resolved.normalized_url, platform=resolved.platform)
        meta = _extract_meta_tags(html)
        article_text = _extract_article_text(html)
        description = meta.get("og:description") or meta.get("description") or ""
        return article_text, description
    except ExtractionError as exc:
        warnings.append(exc.message)
        return "", ""


def extract_with_ytdlp(
    resolved: ResolvedSource,
    capture_dir: Path,
    cookie_text: str = "",
    *,
    progress_callback: ProgressCallback | None = None,
) -> ExtractionOutcome:
    info, warnings = _ytdlp_metadata(
        resolved.normalized_url,
        capture_dir,
        cookie_text=cookie_text,
        platform=resolved.platform,
    )
    strategy = ["metadata"]
    subtitle_text = ""
    subtitle_timeline = ""

    track, subtitle_source, selected_language = _select_subtitle_track(info)
    if track:
        try:
            if progress_callback is not None:
                progress_callback(30, "已找到字幕轨，正在下载字幕。")
            subtitle_text, subtitle_timeline = _download_subtitle(track, capture_dir)
            if subtitle_text:
                strategy.append("subtitle")
                if progress_callback is not None:
                    progress_callback(46, "已拿到可用字幕，正在整理结果。")
        except ExtractionError as exc:
            warnings.append(exc.message)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"字幕下载失败，已回退到媒体处理：{exc}")

    content_type = resolved.content_type
    article_text = ""
    notes_text = (info.get("description") or "").strip()

    if resolved.platform == "xiaohongshu":
        if info.get("duration") or info.get("formats"):
            content_type = "video"
        else:
            content_type = "article"
            page_article, page_description = _supplemental_page_text(resolved, warnings)
            if page_article:
                article_text = page_article
                strategy.append("page_text")
            elif page_description:
                article_text = page_description
                strategy.append("page_description")

    description = notes_text
    result_notes_text = ""
    primary_text = subtitle_text or article_text
    if content_type != "video" and not primary_text:
        primary_text = notes_text
        result_notes_text = notes_text

    outcome = ExtractionOutcome(
        platform=resolved.platform,
        content_type=content_type,
        title=info.get("title") or resolved.normalized_url,
        canonical_url=info.get("webpage_url") or info.get("original_url") or resolved.normalized_url,
        source_item_id=str(info.get("id") or _extract_xiaohongshu_note_id(resolved.normalized_url) or "").strip() or None,
        description=description,
        author=info.get("uploader") or info.get("channel") or info.get("creator"),
        published_at=str(info.get("upload_date") or ""),
        language=info.get("language"),
        thumbnail_url=info.get("thumbnail"),
        duration_seconds=float(info["duration"]) if info.get("duration") else None,
        image_urls=[],
        strategy=strategy,
        warnings=warnings,
        subtitle_text=subtitle_text,
        subtitle_timeline_text=subtitle_timeline,
        subtitle_source=subtitle_source if subtitle_text else "none",
        selected_language=selected_language if subtitle_text else "",
        article_text=article_text,
        notes_text=result_notes_text,
        primary_text=primary_text,
        needs_transcription=content_type in {"video", "audio"} and not bool(subtitle_text),
    )

    should_download_media = content_type == "video" or outcome.needs_transcription
    if should_download_media:
        try:
            if progress_callback is not None:
                progress_callback(36 if content_type == "video" else 34, "正在下载原视频文件。" if content_type == "video" else "正在下载音频文件。")
            media_selection = _download_media_selection_with_ytdlp(
                resolved.normalized_url,
                capture_dir,
                cookie_text=cookie_text,
                platform=resolved.platform,
                content_type=content_type,
            )
            outcome.media_file_path = str(media_selection.path)
            outcome.strategy.append("media_download")
            outcome.provider_traces.append(
                ProviderTraceEntry(
                    stage="extract",
                    level="info",
                    provider="open_source_provider",
                    message="视频下载已完成。" if content_type == "video" else "音频下载已完成。",
                    detail=media_selection.trace_detail(),
                )
            )
            if progress_callback is not None:
                progress_callback(50, "媒体文件已就绪，正在准备后续处理。")
        except DownloadError as exc:
            if subtitle_text or article_text or result_notes_text:
                outcome.warnings.append(f"媒体下载失败，当前仅保留页面可得文本：{exc}")
                outcome.needs_transcription = False
            else:
                raise ExtractionError("download", f"媒体下载失败：{exc}", warnings=outcome.warnings) from exc

    return outcome

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from app.settings import DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT


@dataclass(slots=True)
class ResolvedSource:
    original_input: str
    cleaned_input: str
    normalized_url: str
    platform: str
    content_type: str
    display_platform: str
    detected_urls: list[str] = field(default_factory=list)
    selection_reason: str = ""
    input_warning: str | None = None


PLATFORM_RULES = [
    (("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"), "youtube", "video", "YouTube"),
    (("bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"), "bilibili", "video", "哔哩哔哩"),
    (("xiaoyuzhoufm.com", "www.xiaoyuzhoufm.com"), "xiaoyuzhou", "audio", "小宇宙"),
    (("douyin.com", "www.douyin.com", "v.douyin.com"), "douyin", "video", "抖音"),
    (("xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com"), "xiaohongshu", "webpage", "小红书"),
    (("mp.weixin.qq.com",), "wechat_article", "article", "微信公众号"),
]

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
TRAILING_NOISE = ".,;:!?)]}>\"'，。；：！？】）》」』、"
SHORT_LINK_HOSTS = {"v.douyin.com", "b23.tv", "xhslink.com", "youtu.be"}


def _is_xiaohongshu_landing(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return host.endswith("xiaohongshu.com") and path in {"", "/explore"}


def clean_input_text(raw_text: str) -> str:
    value = (raw_text or "").strip()
    value = value.replace("\u3000", " ")
    value = re.sub(r"[\r\t]+", " ", value)
    value = re.sub(r"\n+", "\n", value)
    return value.strip()


def _trim_noise(url: str) -> str:
    value = url.strip()
    while value and value[-1] in TRAILING_NOISE:
        value = value[:-1]
    return value.strip()


def extract_candidate_urls(raw_text: str) -> list[str]:
    cleaned = clean_input_text(raw_text)
    if not cleaned:
        return []

    pattern = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)
    candidates: list[str] = []
    for match in pattern.finditer(cleaned):
        candidate = _trim_noise(match.group(0))
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def normalize_url(raw_url: str) -> str:
    value = _trim_noise(raw_url)
    if not value:
        raise ValueError("URL cannot be empty.")
    if "://" not in value:
        value = f"https://{value}"
    return value.strip()


def _expand_short_url(url: str) -> str:
    normalized = normalize_url(url)
    host = (urlparse(normalized).netloc or "").lower()
    if host not in SHORT_LINK_HOSTS:
        return normalized

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    try:
        with httpx.Client(
            follow_redirects=True,
            headers=headers,
            timeout=min(DEFAULT_TIMEOUT_SECONDS, 12.0),
            trust_env=False,
        ) as client:
            response = client.get(normalized)
            response.raise_for_status()
            expanded = str(response.url)
            if host == "xhslink.com" and _is_xiaohongshu_landing(expanded):
                return normalized
            return expanded
    except httpx.HTTPError:
        return normalized


def _host_score(url: str) -> tuple[int, int]:
    normalized = normalize_url(url)
    host = (urlparse(normalized).netloc or "").lower()
    for index, (hosts, _, _, _) in enumerate(PLATFORM_RULES):
        if host in hosts:
            return (0, index)
    return (1, 999)


def _select_url(raw_text: str) -> tuple[str, list[str], str, str | None]:
    candidates = extract_candidate_urls(raw_text)
    if not candidates:
        raise ValueError("没有识别到可用链接。请粘贴一个公开网页地址后再试。")

    normalized_candidates = [_expand_short_url(item) for item in candidates]
    if len(normalized_candidates) == 1:
        return normalized_candidates[0], normalized_candidates, "single_url", None

    selected = min(normalized_candidates, key=_host_score)
    warning = f"检测到 {len(normalized_candidates)} 个链接，已优先使用第一条可识别来源链接。"
    return selected, normalized_candidates, "first_supported_url", warning


def resolve_url(raw_input: str) -> ResolvedSource:
    cleaned = clean_input_text(raw_input)
    normalized, detected_urls, selection_reason, warning = _select_url(cleaned)
    host = (urlparse(normalized).netloc or "").lower()

    for hosts, platform, content_type, display_name in PLATFORM_RULES:
        if host in hosts:
            return ResolvedSource(
                original_input=raw_input,
                cleaned_input=cleaned,
                normalized_url=normalized,
                platform=platform,
                content_type=content_type,
                display_platform=display_name,
                detected_urls=detected_urls,
                selection_reason=selection_reason,
                input_warning=warning,
            )

    return ResolvedSource(
        original_input=raw_input,
        cleaned_input=cleaned,
        normalized_url=normalized,
        platform="generic_web",
        content_type="webpage",
        display_platform="网页",
        detected_urls=detected_urls,
        selection_reason=selection_reason,
        input_warning=warning,
    )


def infer_source_item_id(platform: str, normalized_url: str) -> str:
    url = normalized_url or ""
    if platform == "youtube":
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host.endswith("youtu.be"):
            return parsed.path.strip("/").split("/")[0]
        query = parse_qs(parsed.query or "")
        if query.get("v"):
            return query["v"][0]
        match = re.search(r"/shorts/([^/?#]+)", parsed.path or "", re.IGNORECASE)
        return match.group(1) if match else ""
    if platform == "bilibili":
        match = re.search(r"/video/((?:BV|av)[^/?#]+)", url, re.IGNORECASE)
        return match.group(1) if match else ""
    if platform == "xiaoyuzhou":
        match = re.search(r"/episode/([^/?#]+)", url, re.IGNORECASE)
        return match.group(1) if match else ""
    if platform == "douyin":
        match = re.search(r"/video/(\d{10,22})", url, re.IGNORECASE)
        return match.group(1) if match else ""
    if platform == "xiaohongshu":
        for pattern in (r"/explore/([^/?#]+)", r"/discovery/item/([^/?#]+)", r"/note/([^/?#]+)"):
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1)
    if platform == "wechat_article":
        parsed = urlparse(url)
        query = parse_qs(parsed.query or "")
        for key in ("mid", "idx", "sn"):
            values = query.get(key) or []
            if values:
                return values[0]
    return ""


def resolve_file_type(file_name: str) -> tuple[str, str]:
    extension = Path(file_name).suffix.lower()
    if extension in AUDIO_EXTENSIONS:
        return "local_file", "audio"
    if extension in VIDEO_EXTENSIONS:
        return "local_file", "video"
    return "local_file", "unknown"

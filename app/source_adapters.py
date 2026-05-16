from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from app.browser_provider import (
    BrowserProviderError,
    fetch_douyin_media,
    fetch_wechat_page,
    fetch_xiaohongshu_page,
    resolve_xiaohongshu_share_url,
)
from app.extractors import (
    ExtractionError,
    ExtractionOutcome,
    ProviderTraceEntry,
    ProgressCallback,
    extract_douyin_direct,
    extract_generic_web,
    extract_local_file,
    extract_with_ytdlp,
    extract_xiaoyuzhou,
    extract_youtube_transcript,
)
from app.provider_state import provider_is_available, record_provider_failure, record_provider_success
from app.resolver import ResolvedSource
from app.settings import CN_PLATFORM_PROXY, CN_PLATFORM_RELAY_URL, DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT, DEPLOYMENT_MODE


ProviderRunner = Callable[[], ExtractionOutcome]


def _duration_detail(duration_seconds: float, detail: str | None = None) -> str:
    duration_text = f"duration={duration_seconds:.1f}s"
    return f"{duration_text}; {detail}" if detail else duration_text

XIAOHONGSHU_PLACEHOLDER_TITLES = (
    "小红书 - 你的生活兴趣社区",
    "小红书 - 你访问的页面不见了",
)
XIAOHONGSHU_PLACEHOLDER_TEXT = (
    "行吟信息科技（上海）有限公司",
    "地址：上海市黄浦区马当路388号",
    "电话：21-64224530",
)
WECHAT_BLOCKED_TEXT = (
    "此帐号已被屏蔽",
    "此内容因违规无法查看",
    "内容无法查看",
    "投诉",
    "captcha",
    "verify",
    "完成以下验证",
    "环境异常",
)
WECHAT_BLOCKED_URL_MARKERS = ("wappoc_appmsgcaptcha",)


@dataclass(slots=True)
class ProviderSpec:
    name: str
    runner: ProviderRunner


def _provider_label(name: str) -> str:
    return {
        "direct_provider": "直连提取",
        "open_source_provider": "开源解析",
        "browser_provider": "浏览器会话",
        "managed_fallback_provider": "托管兜底",
        "cached_result_provider": "缓存结果",
    }.get(name, name)


def _prepend_warnings(outcome: ExtractionOutcome, warnings: list[str]) -> ExtractionOutcome:
    if warnings:
        outcome.warnings = warnings + list(outcome.warnings)
    return outcome


def _append_provider_trace(
    entries: list[ProviderTraceEntry],
    *,
    level: str,
    provider: str,
    message: str,
    detail: str | None = None,
) -> None:
    entries.append(
        ProviderTraceEntry(
            stage="extract",
            level=level,
            provider=provider,
            message=message,
            detail=detail,
        )
    )


def _browser_error_to_extraction_error(exc: BrowserProviderError) -> ExtractionError:
    return ExtractionError(
        "extract",
        getattr(exc, "message", str(exc)),
        reason_code=getattr(exc, "reason_code", "browser_provider_failed"),
        retryable=getattr(exc, "retryable", True),
    )


def _combined_text(outcome: ExtractionOutcome) -> str:
    return " ".join(
        item.strip()
        for item in (
            outcome.title,
            outcome.primary_text,
            outcome.article_text,
            outcome.notes_text,
            outcome.description,
        )
        if item and item.strip()
    ).lower()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(item.lower() in lowered for item in needles)


def _looks_like_xiaohongshu_placeholder(outcome: ExtractionOutcome) -> bool:
    combined = _combined_text(outcome)
    if _contains_any(combined, XIAOHONGSHU_PLACEHOLDER_TITLES):
        return True
    if _contains_any(combined, XIAOHONGSHU_PLACEHOLDER_TEXT):
        return True
    if outcome.image_urls and all("fe-platform" in item.lower() for item in outcome.image_urls):
        return True
    return False


def _looks_like_wechat_blocked(outcome: ExtractionOutcome) -> bool:
    combined = _combined_text(outcome)
    canonical_url = (outcome.canonical_url or "").lower()
    if _contains_any(combined, WECHAT_BLOCKED_TEXT):
        return True
    return any(marker in canonical_url for marker in WECHAT_BLOCKED_URL_MARKERS)


def _wechat_needs_browser_enrichment(outcome: ExtractionOutcome) -> bool:
    fields = (
        outcome.primary_text,
        outcome.article_text,
        outcome.description,
    )
    if _looks_like_wechat_blocked(outcome):
        return True
    if any("\\x0" in (field or "") for field in fields):
        return True
    return not bool(outcome.image_urls)


def _source_id_from_douyin_url(url: str) -> str:
    match = re.search(r"/video/(\d{10,22})", url or "", re.IGNORECASE)
    return match.group(1) if match else ""


def _source_id_from_xiaohongshu_url(url: str) -> str:
    for pattern in (r"/explore/([^/?#]+)", r"/discovery/item/([^/?#]+)", r"/note/([^/?#]+)"):
        match = re.search(pattern, url or "", re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _xiaohongshu_needs_full_web_url(resolved: ResolvedSource) -> bool:
    parsed = urlparse(resolved.normalized_url)
    host = (parsed.netloc or "").lower()
    if host == "xhslink.com":
        return True
    if not host.endswith("xiaohongshu.com"):
        return False
    note_id = _source_id_from_xiaohongshu_url(resolved.normalized_url)
    return not bool(note_id)


def _xiaohongshu_full_url_error() -> str:
    return "这条小红书内容暂时还不能处理成功，请从浏览器地址栏复制完整网页链接后再试。"


def _recover_xiaohongshu_resolved_source(resolved: ResolvedSource) -> ResolvedSource:
    if not _xiaohongshu_needs_full_web_url(resolved):
        return resolved

    recovered_url = resolve_xiaohongshu_share_url(resolved.normalized_url)
    if not recovered_url:
        return resolved

    recovered = replace(resolved, normalized_url=recovered_url, content_type="webpage")
    if _xiaohongshu_needs_full_web_url(recovered):
        return resolved
    return recovered


def _download_browser_media(
    url: str,
    target_path: Path,
    referer: str,
    *,
    progress_callback: ProgressCallback | None = None,
    progress_start: int = 36,
    progress_end: int = 50,
    platform: str | None = None,
) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": referer,
    }
    client_kwargs = {
        "follow_redirects": True,
        "headers": headers,
        "timeout": DEFAULT_TIMEOUT_SECONDS,
        "trust_env": False,
    }
    if CN_PLATFORM_PROXY and platform in {"douyin", "xiaohongshu"}:
        client_kwargs["proxy"] = CN_PLATFORM_PROXY
    elif DEPLOYMENT_MODE != "local" and platform in {"douyin", "xiaohongshu"}:
        import urllib.parse
        relay_url = f"{CN_PLATFORM_RELAY_URL}?url={urllib.parse.quote(url, safe='')}"
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with httpx.Client(
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=DEFAULT_TIMEOUT_SECONDS * 4,
                trust_env=False,
            ) as client:
                resp = client.get(relay_url)
                resp.raise_for_status()
                with open(target_path, "wb") as handle:
                    handle.write(resp.content)
            if progress_callback is not None:
                progress_callback(progress_end, "原视频下载完成。")
            return target_path
        except httpx.HTTPError as exc:
            last_error = exc
            target_path.unlink(missing_ok=True)
            raise ExtractionError(
                "download",
                f"资源下载失败：{exc}",
                reason_code="browser_media_download_failed",
                retryable=True,
            ) from exc
    last_error: httpx.HTTPError | None = None
    for _ in range(3):
        try:
            target_path.unlink(missing_ok=True)
            with httpx.Client(**client_kwargs) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    response_headers = getattr(response, "headers", {}) or {}
                    total_bytes = int(response_headers.get("Content-Length") or "0")
                    downloaded_bytes = 0
                    last_progress = progress_start
                    if progress_callback is not None:
                        progress_callback(progress_start, "正在下载原视频文件。")
                    with open(target_path, "wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                            downloaded_bytes += len(chunk)
                            if (
                                progress_callback is not None
                                and total_bytes > 0
                                and progress_end > progress_start
                            ):
                                ratio = min(downloaded_bytes / total_bytes, 0.98)
                                mapped = progress_start + int((progress_end - progress_start) * ratio)
                                if mapped >= last_progress + 2:
                                    last_progress = mapped
                                    progress_callback(mapped, "正在下载原视频文件。")
            return target_path
        except httpx.HTTPError as exc:
            last_error = exc

    if last_error is not None:
        target_path.unlink(missing_ok=True)
        raise ExtractionError(
            "download",
            f"下载资源失败：{last_error}",
            reason_code="browser_media_download_failed",
            retryable=True,
        ) from last_error
    return target_path


def _require_media_or_subtitle(outcome: ExtractionOutcome, message: str) -> ExtractionOutcome:
    if outcome.subtitle_text.strip():
        return outcome
    media_path = outcome.media_file_path or ""
    if media_path and Path(media_path).exists():
        return outcome
    raise ExtractionError("extract", message, reason_code="media_or_subtitle_missing", retryable=False)


def _require_media_subtitle_or_notes(outcome: ExtractionOutcome, message: str) -> ExtractionOutcome:
    if outcome.subtitle_text.strip() or outcome.primary_text.strip():
        return outcome
    media_path = outcome.media_file_path or ""
    if media_path and Path(media_path).exists():
        return outcome
    raise ExtractionError("extract", message, reason_code="media_or_subtitle_missing", retryable=False)


def _require_images(outcome: ExtractionOutcome, message: str) -> ExtractionOutcome:
    if outcome.image_urls:
        return outcome
    raise ExtractionError("extract", message, reason_code="image_assets_missing", retryable=False)


def _require_text_or_images(outcome: ExtractionOutcome, message: str) -> ExtractionOutcome:
    if outcome.primary_text.strip() or outcome.article_text.strip() or outcome.image_urls:
        return outcome
    raise ExtractionError("extract", message, reason_code="text_or_images_missing", retryable=False)


def _looks_like_douyin_chapter_outline(value: str) -> bool:
    text = (value or "").strip()
    if not text or text.count("\n") < 2:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    return all(re.match(r"^\d{2}:\d{2}(?::\d{2})?\s+\S", line) for line in lines)


class BaseSourceAdapter:
    platform = "generic_web"

    def build_providers(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProviderSpec]:
        raise NotImplementedError

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        return outcome

    def extract(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> ExtractionOutcome:
        failures: list[str] = []
        last_error: ExtractionError | None = None
        cooled_down_providers: list[ProviderSpec] = []
        attempted_providers: list[str] = []
        provider_traces: list[ProviderTraceEntry] = []

        for provider in self.build_providers(
            resolved,
            capture_dir,
            cookie_text=cookie_text,
            progress_callback=progress_callback,
        ):
            health_key = f"{self.platform}:{provider.name}"
            if not provider_is_available(health_key):
                failures.append(f"{_provider_label(provider.name)}当前正在冷却，已自动跳过。")
                cooled_down_providers.append(provider)
                _append_provider_trace(
                    provider_traces,
                    level="warning",
                    provider=provider.name,
                    message="提取器当前处于冷却中，已自动跳过。",
                )
                continue

            provider_started = 0.0
            try:
                attempted_providers.append(provider.name)
                if progress_callback is not None:
                    provider_index = max(len(attempted_providers) - 1, 0)
                    progress_callback(
                        min(48, 18 + provider_index * 12),
                        f"正在尝试{_provider_label(provider.name)}。",
                    )
                _append_provider_trace(
                    provider_traces,
                    level="info",
                    provider=provider.name,
                    message=f"开始尝试{_provider_label(provider.name)}。",
                )
                provider_started = time.perf_counter()
                outcome = provider.runner()
                provider_duration = time.perf_counter() - provider_started
                outcome = self.validate_outcome(resolved, outcome)
                record_provider_success(health_key)
                outcome.extractor_used = outcome.extractor_used or provider.name
                outcome.fallback_chain = list(dict.fromkeys([*attempted_providers, *outcome.fallback_chain]))
                outcome.fallback_used = outcome.fallback_used or len(outcome.fallback_chain) > 1
                strategy_detail = " -> ".join(outcome.strategy) if outcome.strategy else None
                provider_traces.append(
                    ProviderTraceEntry(
                        stage="extract",
                        level="info",
                        provider=provider.name,
                        message=f"{_provider_label(provider.name)}提取成功。",
                        detail=_duration_detail(provider_duration, strategy_detail),
                    )
                )
                outcome.provider_traces = [*provider_traces, *outcome.provider_traces]
                return _prepend_warnings(outcome, failures)
            except BrowserProviderError as exc:
                provider_duration = time.perf_counter() - provider_started if provider_started else 0.0
                record_provider_failure(health_key)
                last_error = _browser_error_to_extraction_error(exc)
                failures.append(f"{_provider_label(provider.name)}失败：{exc.message}")
                _append_provider_trace(
                    provider_traces,
                    level="warning",
                    provider=provider.name,
                    message=f"{_provider_label(provider.name)}失败。",
                    detail=_duration_detail(provider_duration, exc.message),
                )
            except ExtractionError as exc:
                provider_duration = time.perf_counter() - provider_started if provider_started else 0.0
                record_provider_failure(health_key)
                last_error = exc
                failures.append(f"{_provider_label(provider.name)}失败：{exc.message}")
                _append_provider_trace(
                    provider_traces,
                    level="warning",
                    provider=provider.name,
                    message=f"{_provider_label(provider.name)}失败。",
                    detail=_duration_detail(provider_duration, exc.message),
                )

        if last_error is not None:
            last_error.warnings = failures + list(last_error.warnings)
            last_error.provider_traces = [*provider_traces, *last_error.provider_traces]
            raise last_error

        if cooled_down_providers:
            for provider in cooled_down_providers:
                provider_started = 0.0
                try:
                    attempted_providers.append(provider.name)
                    if progress_callback is not None:
                        provider_index = max(len(attempted_providers) - 1, 0)
                        progress_callback(
                            min(50, 20 + provider_index * 10),
                            f"正在重新尝试{_provider_label(provider.name)}。",
                        )
                    _append_provider_trace(
                        provider_traces,
                        level="info",
                        provider=provider.name,
                        message=f"重新尝试{_provider_label(provider.name)}。",
                    )
                    provider_started = time.perf_counter()
                    outcome = provider.runner()
                    provider_duration = time.perf_counter() - provider_started
                    outcome = self.validate_outcome(resolved, outcome)
                    outcome.extractor_used = outcome.extractor_used or provider.name
                    outcome.fallback_chain = list(dict.fromkeys([*attempted_providers, *outcome.fallback_chain]))
                    outcome.fallback_used = outcome.fallback_used or len(outcome.fallback_chain) > 1
                    strategy_detail = " -> ".join(outcome.strategy) if outcome.strategy else None
                    provider_traces.append(
                        ProviderTraceEntry(
                            stage="extract",
                            level="info",
                            provider=provider.name,
                            message=f"{_provider_label(provider.name)}提取成功。",
                            detail=_duration_detail(provider_duration, strategy_detail),
                        )
                    )
                    outcome.provider_traces = [*provider_traces, *outcome.provider_traces]
                    return _prepend_warnings(outcome, failures)
                except BrowserProviderError as exc:
                    provider_duration = time.perf_counter() - provider_started if provider_started else 0.0
                    last_error = _browser_error_to_extraction_error(exc)
                    failures.append(f"{_provider_label(provider.name)}失败：{exc.message}")
                    _append_provider_trace(
                        provider_traces,
                        level="warning",
                        provider=provider.name,
                        message=f"{_provider_label(provider.name)}失败。",
                        detail=_duration_detail(provider_duration, exc.message),
                    )
                except ExtractionError as exc:
                    provider_duration = time.perf_counter() - provider_started if provider_started else 0.0
                    last_error = exc
                    failures.append(f"{_provider_label(provider.name)}失败：{exc.message}")
                    _append_provider_trace(
                        provider_traces,
                        level="warning",
                        provider=provider.name,
                        message=f"{_provider_label(provider.name)}失败。",
                        detail=_duration_detail(provider_duration, exc.message),
                    )
            if last_error is not None:
                last_error.warnings = failures + list(last_error.warnings)
                last_error.provider_traces = [*provider_traces, *last_error.provider_traces]
                raise last_error

        raise ExtractionError(
            "extract",
            f"{self.platform} 没有可用提取器。",
            warnings=failures,
            reason_code="no_provider_available",
            retryable=True,
            provider_traces=provider_traces,
        )


class LocalFileAdapter(BaseSourceAdapter):
    platform = "local_file"

    def __init__(self, content_type: str) -> None:
        self.content_type = content_type

    def extract_file(self, file_path: str) -> ExtractionOutcome:
        outcome = extract_local_file(file_path, content_type=self.content_type)
        outcome.extractor_used = "local_file_provider"
        outcome.fallback_chain = ["local_file_provider"]
        return outcome


class GenericWebAdapter(BaseSourceAdapter):
    platform = "generic_web"

    def build_providers(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProviderSpec]:
        return [ProviderSpec("direct_provider", lambda: extract_generic_web(resolved))]

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        if resolved.platform == "wechat_article":
            if _looks_like_wechat_blocked(outcome):
                raise ExtractionError(
                    "extract",
                    "这篇微信公众号文章当前不可访问，请换一篇公开文章再试。",
                    reason_code="wechat_article_blocked",
                    retryable=False,
                )
            return _require_text_or_images(outcome, "微信公众号文章没有提取到可用正文或图片。")
        return _require_text_or_images(outcome, "网页内容没有提取到可用正文。")


class WechatArticleAdapter(BaseSourceAdapter):
    platform = "wechat_article"

    def _browser_extract(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> ExtractionOutcome:
        if progress_callback is not None:
            progress_callback(30, "正在通过浏览器会话补全正文和图片。")
        browser_result = fetch_wechat_page(resolved.normalized_url)
        article_text = browser_result.body_text[:5000].strip()
        image_urls = browser_result.image_urls
        return ExtractionOutcome(
            platform="wechat_article",
            content_type="image_article" if image_urls else "article",
            title=browser_result.title,
            canonical_url=browser_result.final_url,
            description=(article_text[:140] if article_text else ""),
            language="zh",
            image_urls=image_urls,
            strategy=["browser_session", "page_images", "page_text"] if image_urls else ["browser_session", "page_text"],
            article_text=article_text,
            primary_text=article_text,
            needs_transcription=False,
        )

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        if _looks_like_wechat_blocked(outcome):
            raise ExtractionError(
                "extract",
                "这篇微信公众号文章当前不可访问，请换一篇公开文章再试。",
                reason_code="wechat_article_blocked",
                retryable=False,
            )
        return _require_text_or_images(outcome, "微信公众号文章没有提取到可用正文或图片。")

    def extract(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> ExtractionOutcome:
        failures: list[str] = []
        provider_traces: list[ProviderTraceEntry] = []
        direct_outcome: ExtractionOutcome | None = None
        direct_error: ExtractionError | None = None

        _append_provider_trace(provider_traces, level="info", provider="direct_provider", message="开始尝试直连提取。")
        try:
            if progress_callback is not None:
                progress_callback(18, "正在尝试直连提取。")
            direct_outcome = self.validate_outcome(resolved, extract_generic_web(resolved))
            if not _wechat_needs_browser_enrichment(direct_outcome):
                direct_outcome.extractor_used = "direct_provider"
                direct_outcome.fallback_chain = ["direct_provider"]
                direct_outcome.provider_traces = [
                    *provider_traces,
                    ProviderTraceEntry(stage="extract", level="info", provider="direct_provider", message="直连提取成功。"),
                    *direct_outcome.provider_traces,
                ]
                return direct_outcome
            failures.append("直连提取只拿到了部分公众号内容，已自动尝试浏览器会话补全。")
            _append_provider_trace(
                provider_traces,
                level="warning",
                provider="direct_provider",
                message="直连结果不完整，继续尝试浏览器会话。",
            )
        except ExtractionError as exc:
            direct_error = exc
            failures.append(f"直连提取失败：{exc.message}")
            _append_provider_trace(
                provider_traces,
                level="warning",
                provider="direct_provider",
                message="直连提取失败。",
                detail=exc.message,
            )

        _append_provider_trace(provider_traces, level="info", provider="browser_provider", message="开始尝试浏览器会话。")
        try:
            if progress_callback is not None:
                progress_callback(30, "正在尝试浏览器会话。")
            browser_outcome = self.validate_outcome(
                resolved,
                self._browser_extract(resolved, capture_dir, progress_callback=progress_callback),
            )
            browser_outcome.extractor_used = "browser_provider"
            browser_outcome.fallback_chain = ["direct_provider", "browser_provider"] if direct_outcome or direct_error else ["browser_provider"]
            browser_outcome.fallback_used = len(browser_outcome.fallback_chain) > 1
            browser_outcome.provider_traces = [
                *provider_traces,
                ProviderTraceEntry(stage="extract", level="info", provider="browser_provider", message="浏览器会话提取成功。"),
                *browser_outcome.provider_traces,
            ]
            return _prepend_warnings(browser_outcome, failures)
        except BrowserProviderError as exc:
            browser_error = _browser_error_to_extraction_error(exc)
            failures.append(f"浏览器会话失败：{exc.message}")
            _append_provider_trace(
                provider_traces,
                level="warning",
                provider="browser_provider",
                message="浏览器会话提取失败。",
                detail=exc.message,
            )
            if direct_outcome is not None:
                direct_outcome.extractor_used = "direct_provider"
                direct_outcome.fallback_chain = ["direct_provider"]
                direct_outcome.provider_traces = [*provider_traces, *direct_outcome.provider_traces]
                return _prepend_warnings(direct_outcome, failures)
            browser_error.warnings = failures + list(browser_error.warnings)
            browser_error.provider_traces = [*provider_traces, *browser_error.provider_traces]
            raise browser_error
        except ExtractionError as exc:
            failures.append(f"浏览器会话失败：{exc.message}")
            _append_provider_trace(
                provider_traces,
                level="warning",
                provider="browser_provider",
                message="浏览器会话提取失败。",
                detail=exc.message,
            )
            if direct_outcome is not None:
                direct_outcome.extractor_used = "direct_provider"
                direct_outcome.fallback_chain = ["direct_provider"]
                direct_outcome.provider_traces = [*provider_traces, *direct_outcome.provider_traces]
                return _prepend_warnings(direct_outcome, failures)
            exc.warnings = failures + list(exc.warnings)
            exc.provider_traces = [*provider_traces, *exc.provider_traces]
            raise exc


class YouTubeAdapter(BaseSourceAdapter):
    platform = "youtube"

    def _subtitle_first_extract(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> ExtractionOutcome:
        outcome = extract_youtube_transcript(resolved, progress_callback=progress_callback)
        try:
            media_outcome = extract_with_ytdlp(
                resolved,
                capture_dir,
                cookie_text=cookie_text,
                progress_callback=progress_callback,
            )
        except ExtractionError as exc:
            outcome.warnings.append(f"原视频下载失败，当前仅保留字幕结果：{exc.message}")
            return outcome

        if media_outcome.media_file_path and Path(media_outcome.media_file_path).exists():
            outcome.media_file_path = media_outcome.media_file_path
            outcome.strategy = list(dict.fromkeys([*outcome.strategy, "media_download"]))

        if media_outcome.duration_seconds:
            outcome.duration_seconds = media_outcome.duration_seconds
        if media_outcome.thumbnail_url:
            outcome.thumbnail_url = media_outcome.thumbnail_url
        if media_outcome.author:
            outcome.author = media_outcome.author
        if media_outcome.description:
            outcome.description = media_outcome.description
        if media_outcome.warnings:
            outcome.warnings.extend(media_outcome.warnings)
        return outcome

    def build_providers(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProviderSpec]:
        return [
            ProviderSpec(
                "direct_provider",
                lambda: self._subtitle_first_extract(
                    resolved,
                    capture_dir,
                    cookie_text=cookie_text,
                    progress_callback=progress_callback,
                ),
            ),
            ProviderSpec(
                "open_source_provider",
                lambda: extract_with_ytdlp(
                    resolved,
                    capture_dir,
                    cookie_text=cookie_text,
                    progress_callback=progress_callback,
                ),
            ),
        ]

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        return _require_media_or_subtitle(outcome, "YouTube 视频没有拿到字幕或可转写媒体。")


class BilibiliAdapter(BaseSourceAdapter):
    platform = "bilibili"

    def build_providers(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProviderSpec]:
        return [
            ProviderSpec(
                "open_source_provider",
                lambda: extract_with_ytdlp(
                    resolved,
                    capture_dir,
                    cookie_text=cookie_text,
                    progress_callback=progress_callback,
                ),
            )
        ]

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        return _require_media_or_subtitle(outcome, "哔哩哔哩视频没有拿到字幕或可转写媒体。")


class XiaoyuzhouAdapter(BaseSourceAdapter):
    platform = "xiaoyuzhou"

    def build_providers(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProviderSpec]:
        return [
            ProviderSpec("direct_provider", lambda: extract_xiaoyuzhou(resolved, capture_dir)),
            ProviderSpec(
                "open_source_provider",
                lambda: extract_with_ytdlp(
                    resolved,
                    capture_dir,
                    cookie_text=cookie_text,
                    progress_callback=progress_callback,
                ),
            ),
        ]

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        outcome.description = ""
        outcome.notes_text = ""
        outcome.article_text = ""
        outcome.primary_text = ""
        return _require_media_or_subtitle(outcome, "小宇宙没有拿到可转写音频。")


class DouyinAdapter(BaseSourceAdapter):
    platform = "douyin"

    def _browser_extract(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> ExtractionOutcome:
        if progress_callback is not None:
            progress_callback(28, "正在通过浏览器会话解析页面。")
        browser_result = fetch_douyin_media(resolved.normalized_url)
        page_text = browser_result.body_text.strip()
        chapter_outline = _looks_like_douyin_chapter_outline(page_text)
        notes_text = "" if chapter_outline else page_text[:280].strip()
        warnings = ["检测到章节目录，已忽略并继续转写原视频。"] if chapter_outline else []
        if browser_result.media_url:
            media_path = capture_dir / "douyin_browser_source.mp4"
            if progress_callback is not None:
                progress_callback(36, "正在下载原视频文件。")
            _download_browser_media(
                browser_result.media_url,
                media_path,
                browser_result.final_url,
                progress_callback=progress_callback,
                progress_start=36,
                progress_end=50,
                platform="douyin",
            )
            if progress_callback is not None:
                progress_callback(50, "原视频已就绪，正在准备转写。")
            return ExtractionOutcome(
                platform="douyin",
                content_type="video",
                title=browser_result.title,
                canonical_url=browser_result.final_url,
                source_item_id=_source_id_from_douyin_url(browser_result.final_url) or None,
                description=(notes_text[:140] if notes_text else ""),
                language="zh",
                image_urls=[],
                live_photo_video_urls=[],
                strategy=(
                    ["share_resolve", "browser_session", "chapter_outline", "media_direct"]
                    if chapter_outline
                    else ["share_resolve", "browser_session", "media_direct"]
                ),
                warnings=warnings,
                notes_text=notes_text,
                primary_text="",
                subtitle_text="",
                subtitle_source="none",
                selected_language=None,
                media_file_path=str(media_path),
                needs_transcription=True,
            )
        return ExtractionOutcome(
            platform="douyin",
            content_type="image_article",
            title=browser_result.title,
            canonical_url=browser_result.final_url,
            source_item_id=_source_id_from_douyin_url(browser_result.final_url) or None,
            description=(notes_text[:140] if notes_text else ""),
            language="zh",
            image_urls=browser_result.image_urls,
            live_photo_video_urls=browser_result.live_photo_video_urls,
            strategy=["share_resolve", "browser_session", "page_images"],
            notes_text=notes_text,
            primary_text=notes_text,
            needs_transcription=False,
        )

    def build_providers(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProviderSpec]:
        return [
            ProviderSpec(
                "direct_provider",
                lambda: extract_douyin_direct(
                    resolved,
                    capture_dir,
                    progress_callback=progress_callback,
                    metadata_timeout_seconds=45.0,
                ),
            ),
            ProviderSpec(
                "browser_provider",
                lambda: self._browser_extract(resolved, capture_dir, progress_callback=progress_callback),
            ),
            ProviderSpec(
                "open_source_provider",
                lambda: extract_with_ytdlp(
                    resolved,
                    capture_dir,
                    cookie_text=cookie_text,
                    progress_callback=progress_callback,
                ),
            ),
        ]

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        if outcome.content_type == "image_article":
            return _require_images(outcome, "抖音图文没有拿到可用图片。")
        return _require_media_or_subtitle(outcome, "抖音视频没有拿到可转写媒体或字幕。")


class XiaohongshuAdapter(BaseSourceAdapter):
    platform = "xiaohongshu"

    def _browser_extract(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> ExtractionOutcome:
        if progress_callback is not None:
            progress_callback(28, "正在通过浏览器会话解析页面。")
        browser_result = fetch_xiaohongshu_page(resolved.normalized_url)
        note_id = _source_id_from_xiaohongshu_url(browser_result.final_url) or None
        article_text = browser_result.body_text[:5000].strip()
        if browser_result.media_url:
            media_path = capture_dir / "xiaohongshu_browser_source.mp4"
            if progress_callback is not None:
                progress_callback(36, "正在下载原视频文件。")
            _download_browser_media(
                browser_result.media_url,
                media_path,
                browser_result.final_url,
                progress_callback=progress_callback,
                progress_start=36,
                progress_end=50,
                platform="xiaohongshu",
            )
            if progress_callback is not None:
                progress_callback(50, "原视频已就绪，正在准备转写。")
            return ExtractionOutcome(
                platform="xiaohongshu",
                content_type="video",
                title=browser_result.title,
                canonical_url=browser_result.final_url,
                source_item_id=note_id,
                description=(article_text[:140] if article_text else ""),
                language="zh",
                image_urls=[],
                strategy=["browser_session", "media_direct"],
                article_text=article_text,
                notes_text="",
                primary_text="",
                media_file_path=str(media_path),
                needs_transcription=True,
            )
        return ExtractionOutcome(
            platform="xiaohongshu",
            content_type="image_article" if browser_result.image_urls else "article",
            title=browser_result.title,
            canonical_url=browser_result.final_url,
            source_item_id=note_id,
            description=(article_text[:140] if article_text else ""),
            language="zh",
            image_urls=browser_result.image_urls,
            live_photo_video_urls=browser_result.live_photo_video_urls,
            strategy=["browser_session", "page_text"],
            article_text=article_text,
            primary_text=article_text,
            needs_transcription=False,
        )

    def build_providers(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProviderSpec]:
        return [
            ProviderSpec(
                "browser_provider",
                lambda: self._browser_extract(resolved, capture_dir, progress_callback=progress_callback),
            ),
            ProviderSpec(
                "open_source_provider",
                lambda: extract_with_ytdlp(
                    resolved,
                    capture_dir,
                    cookie_text=cookie_text,
                    progress_callback=progress_callback,
                ),
            ),
        ]

    def extract(
        self,
        resolved: ResolvedSource,
        capture_dir: Path,
        *,
        cookie_text: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> ExtractionOutcome:
        resolved = _recover_xiaohongshu_resolved_source(resolved)
        if _xiaohongshu_needs_full_web_url(resolved):
            raise ExtractionError(
                "extract",
                _xiaohongshu_full_url_error(),
                reason_code="xiaohongshu_full_url_required",
                retryable=False,
            )

        browser_error: str | None = None
        browser_outcome: ExtractionOutcome | None = None
        provider_traces: list[ProviderTraceEntry] = [
            ProviderTraceEntry(stage="extract", level="info", provider="browser_provider", message="开始尝试浏览器会话。")
        ]
        try:
            if progress_callback is not None:
                progress_callback(18, "正在尝试浏览器会话。")
            browser_outcome = self.validate_outcome(
                resolved,
                self._browser_extract(resolved, capture_dir, progress_callback=progress_callback),
            )
            browser_outcome.extractor_used = "browser_provider"
            browser_outcome.fallback_chain = ["browser_provider"]
            provider_traces.append(
                ProviderTraceEntry(stage="extract", level="info", provider="browser_provider", message="浏览器会话提取成功。")
            )
            browser_outcome.provider_traces = list(provider_traces)
            if browser_outcome.content_type != "video":
                return browser_outcome
        except BrowserProviderError as exc:
            browser_error = exc.message
            provider_traces.append(
                ProviderTraceEntry(
                    stage="extract",
                    level="warning",
                    provider="browser_provider",
                    message="浏览器会话提取失败。",
                    detail=browser_error,
                )
            )
        except ExtractionError as exc:
            browser_error = exc.message
            provider_traces.append(
                ProviderTraceEntry(
                    stage="extract",
                    level="warning",
                    provider="browser_provider",
                    message="浏览器会话提取失败。",
                    detail=browser_error,
                )
            )

        try:
            provider_traces.append(
                ProviderTraceEntry(stage="extract", level="info", provider="open_source_provider", message="开始尝试开源解析。")
            )
            if progress_callback is not None:
                progress_callback(32, "正在尝试开源解析。")
            outcome = self.validate_outcome(
                resolved,
                extract_with_ytdlp(
                    resolved,
                    capture_dir,
                    cookie_text=cookie_text,
                    progress_callback=progress_callback,
                ),
            )
            outcome.extractor_used = "open_source_provider"
            outcome.fallback_chain = ["browser_provider", "open_source_provider"] if browser_error else ["open_source_provider"]
            outcome.fallback_used = bool(browser_error)
            provider_traces.append(
                ProviderTraceEntry(
                    stage="extract",
                    level="info",
                    provider="open_source_provider",
                    message="开源解析提取成功。",
                    detail=" -> ".join(outcome.strategy) if outcome.strategy else None,
                )
            )
            outcome.provider_traces = list(provider_traces)
            return _prepend_warnings(outcome, [browser_error] if browser_error else [])
        except ExtractionError as exc:
            provider_traces.append(
                ProviderTraceEntry(
                    stage="extract",
                    level="warning",
                    provider="open_source_provider",
                    message="开源解析提取失败。",
                    detail=exc.message,
                )
            )
            if browser_outcome is not None:
                browser_outcome.fallback_used = False
                browser_outcome.provider_traces = list(provider_traces)
                return _prepend_warnings(browser_outcome, [browser_error] if browser_error else [])
            exc.warnings = ([browser_error] if browser_error else []) + list(exc.warnings)
            exc.provider_traces = list(provider_traces)
            raise

    def validate_outcome(self, resolved: ResolvedSource, outcome: ExtractionOutcome) -> ExtractionOutcome:
        if _xiaohongshu_needs_full_web_url(resolved):
            raise ExtractionError(
                "extract",
                _xiaohongshu_full_url_error(),
                reason_code="xiaohongshu_full_url_required",
                retryable=False,
            )
        if _looks_like_xiaohongshu_placeholder(outcome):
            raise ExtractionError(
                "extract",
                "这条小红书内容当前不可访问，或页面已经失效。请换一条公开图文或视频再试。",
                reason_code="xiaohongshu_placeholder_page",
                retryable=False,
            )
        if outcome.content_type == "video":
            if outcome.subtitle_text.strip():
                return outcome
            media_path = outcome.media_file_path or ""
            if media_path and Path(media_path).exists():
                return outcome
            raise ExtractionError(
                "extract",
                "小红书视频没有拿到可转写媒体或字幕。当前预览环境可能无法直接访问中国平台，请稍后重试。",
                reason_code="media_or_subtitle_missing",
                retryable=True,
            )
        if outcome.image_urls:
            return outcome
        if outcome.primary_text.strip() or (outcome.article_text or "").strip():
            return outcome
        raise ExtractionError(
            "extract",
            '小红书图文没有拿到正文或图片。请确认链接来自小红书 App 的"复制链接"功能，而非浏览器地址栏。',
            reason_code="text_or_images_missing",
            retryable=False,
        )


def get_url_adapter(resolved: ResolvedSource) -> BaseSourceAdapter:
    if resolved.platform == "youtube":
        return YouTubeAdapter()
    if resolved.platform == "bilibili":
        return BilibiliAdapter()
    if resolved.platform == "xiaoyuzhou":
        return XiaoyuzhouAdapter()
    if resolved.platform == "douyin":
        return DouyinAdapter()
    if resolved.platform == "xiaohongshu":
        return XiaohongshuAdapter()
    if resolved.platform == "wechat_article":
        return WechatArticleAdapter()
    return GenericWebAdapter()

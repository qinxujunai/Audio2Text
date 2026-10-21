from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from html import unescape
from typing import Literal
from urllib.parse import unquote, urljoin, urlparse

import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.settings import (
    BROWSER_PROFILE_DIR,
    BROWSER_RUNTIME_IDLE_SECONDS,
    BROWSER_RUNTIME_MAX_TASKS,
    CN_PLATFORM_PROXY,
    CN_PLATFORM_RELAY_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    DEPLOYMENT_MODE,
)


class BrowserProviderError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str = "browser_provider_failed", retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(slots=True)
class BrowserMediaResult:
    final_url: str
    title: str
    media_url: str
    body_text: str
    image_urls: list[str]
    live_photo_video_urls: list[str]


BROWSER_GOTO_TIMEOUT_MS = 45000
BROWSER_SETTLE_WAIT_MS = 1800
BROWSER_NETWORK_IDLE_TIMEOUT_MS = 2500
BrowserFailureCode = Literal[
    "browser_runtime_missing",
    "browser_timeout",
    "browser_network_failed",
    "browser_rate_limited",
    "browser_server_unavailable",
    "browser_challenge_required",
    "browser_network_risk",
    "browser_session_expired",
    "source_not_found",
    "source_access_restricted",
    "browser_request_failed",
    "browser_provider_failed",
]


class BrowserRuntime:
    """Serial, persistent Playwright runtime shared by capture tasks in this process."""

    def __init__(self, *, max_tasks: int, idle_seconds: int) -> None:
        self.max_tasks = max_tasks
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._playwright = None
        self._context = None
        self._owner_thread_id: int | None = None
        self._channel = ""
        self._started_monotonic = 0.0
        self._last_used_monotonic = 0.0
        self._task_count = 0
        self._restart_count = 0
        self._active = False
        self._needs_restart = False
        self._last_error_code = ""

    def _stop_locked(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = None
        self._playwright = None
        self._owner_thread_id = None
        self._channel = ""
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _start_locked(self) -> None:
        self._stop_locked()
        playwright = sync_playwright().start()
        last_exc: Exception | None = None
        context = None
        selected_channel = "bundled"
        base_options = {
            "user_data_dir": str(BROWSER_PROFILE_DIR),
            "headless": True,
            "viewport": {"width": 1360, "height": 920},
            "args": [
                "--no-sandbox",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
            ],
        }
        for channel in ("msedge", "chrome", None):
            launch_options = dict(base_options)
            if channel:
                launch_options["channel"] = channel
            try:
                context = playwright.chromium.launch_persistent_context(**launch_options)
                selected_channel = channel or "bundled"
                break
            except Exception as exc:
                last_exc = exc
        if context is None:
            playwright.stop()
            self._last_error_code = "browser_runtime_missing"
            raise _normalize_browser_exception(last_exc or RuntimeError("browser launch failed"))

        self._playwright = playwright
        self._context = context
        self._owner_thread_id = threading.get_ident()
        self._channel = selected_channel
        self._started_monotonic = time.monotonic()
        self._last_used_monotonic = self._started_monotonic
        self._task_count = 0
        self._needs_restart = False
        self._last_error_code = ""

    def _should_recycle_locked(self, now: float) -> bool:
        if self._context is None or self._needs_restart:
            return True
        if self._task_count >= self.max_tasks:
            return True
        return bool(
            self._last_used_monotonic
            and now - self._last_used_monotonic >= self.idle_seconds
        )

    @contextmanager
    def lease(self):
        with self._lock:
            now = time.monotonic()
            if self._context is not None and self._owner_thread_id != threading.get_ident():
                self._needs_restart = True
                self._last_error_code = "browser_thread_changed"
            if self._should_recycle_locked(now):
                if self._context is not None:
                    self._restart_count += 1
                self._start_locked()

            context = self._context
            if context is None:
                raise _structured_browser_error(
                    "浏览器运行时未能启动。",
                    reason_code="browser_runtime_missing",
                    retryable=True,
                )
            for existing_page in list(context.pages):
                try:
                    existing_page.close()
                except Exception:
                    pass
            context.new_page()
            self._active = True
            try:
                yield context
            except Exception as exc:
                lowered = str(exc).lower()
                if any(token in lowered for token in ("browser has been closed", "target closed", "connection closed")):
                    self._needs_restart = True
                    self._last_error_code = "browser_runtime_crashed"
                raise
            finally:
                for page in list(context.pages):
                    try:
                        page.close()
                    except Exception:
                        self._needs_restart = True
                self._task_count += 1
                self._last_used_monotonic = time.monotonic()
                self._active = False

    def status(self) -> dict[str, object]:
        with self._lock:
            now = time.monotonic()
            state = "restarting" if self._needs_restart else ("ready" if self._context is not None else "idle")
            return {
                "state": state,
                "active": self._active,
                "channel": self._channel or None,
                "tasks_since_restart": self._task_count,
                "restart_count": self._restart_count,
                "uptime_seconds": round(now - self._started_monotonic, 3) if self._started_monotonic else 0.0,
                "idle_seconds": round(now - self._last_used_monotonic, 3) if self._last_used_monotonic else None,
                "max_tasks": self.max_tasks,
                "last_error_code": self._last_error_code or None,
            }

    def shutdown(self) -> None:
        with self._lock:
            self._stop_locked()
            self._active = False
            self._needs_restart = False

    def refresh(self) -> None:
        """Drop the project session so the next lease starts a clean context.

        Platform challenges are tied to a browser context as well as to the
        page.  Marking the context for recycling is not enough when a caller
        needs an immediate retry, so this method closes it synchronously and
        leaves the profile on disk intact.
        """
        with self._lock:
            if self._context is not None:
                self._restart_count += 1
            self._stop_locked()
            self._active = False
            self._needs_restart = False
            self._last_error_code = "manual_refresh"


_browser_runtime = BrowserRuntime(
    max_tasks=BROWSER_RUNTIME_MAX_TASKS,
    idle_seconds=BROWSER_RUNTIME_IDLE_SECONDS,
)


def browser_runtime_status() -> dict[str, object]:
    return _browser_runtime.status()


def shutdown_browser_runtime() -> None:
    _browser_runtime.shutdown()


def refresh_browser_runtime() -> None:
    """Refresh the isolated browser context without deleting its profile."""
    _browser_runtime.refresh()


def _normalize_text(value: str) -> str:
    lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
    deduped: list[str] = []
    for line in lines:
        if line not in deduped:
            deduped.append(line)
    return "\n".join(deduped)


def _prefer_https(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _looks_like_xiaohongshu_full_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower().rstrip(".")
    path = (parsed.path or "").lower()
    return (
        host.endswith("xiaohongshu.com")
        and any(path.startswith(token) for token in ("/explore/", "/discovery/item/", "/note/"))
    )


def _is_xiaohongshu_note_less_landing(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower().rstrip(".")
    path = (parsed.path or "/").rstrip("/").lower() or "/"
    return (
        (host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"))
        and path in {"", "/", "/login", "/explore", "/discovery", "/404"}
    )


def _is_noise_image(candidate: str, marker: str) -> bool:
    lowered = candidate.lower()
    if any(
        token in lowered
        for token in (
            "fe-platform",
            "favicon",
            "avatar",
            "sprite",
            "logo",
            "badge",
            "icon",
        )
    ):
        return True
    if "interactive" in marker:
        return True
    return False


def _structured_browser_error(
    message: str,
    *,
    reason_code: BrowserFailureCode,
    retryable: bool,
) -> BrowserProviderError:
    return BrowserProviderError(message, reason_code=reason_code, retryable=retryable)


def _page_state_error(final_url: str, title: str, body_text: str) -> BrowserProviderError | None:
    combined = "\n".join(item for item in (final_url, title, body_text) if item).lower()
    network_risk_markers = (
        "ip存在风险",
        "安全限制",
        "error_code=300012",
    )
    unavailable_markers = (
        "error_code=300031",
        "当前笔记暂时无法浏览",
        "当前内容暂时无法浏览",
        "内容暂时无法浏览",
        "笔记不存在",
    )
    challenge_markers = (
        "captcha",
        "verify",
        "verification",
        "security check",
        "安全验证",
        "请完成验证",
        "请先验证",
        "验证码",
        "异常访问",
    )
    session_markers = (
        "sign in",
        "log in",
        "login",
        "登录后",
        "请先登录",
        "扫码登录",
        "登录即可",
        "登录可见",
    )
    restricted_markers = (
        "private",
        "权限",
        "仅自己可见",
        "仅好友可见",
        "访问受限",
        "内容不可见",
        "无权查看",
    )
    missing_markers = (
        "页面不存在",
        "内容不存在",
        "not found",
        "page not found",
        "已删除",
        "不见了",
        "已失效",
    )

    if any(marker in combined for marker in network_risk_markers):
        return _structured_browser_error(
            "当前网络被平台判定为高风险，请切换到可靠网络后重试。",
            reason_code="browser_network_risk",
            retryable=True,
        )
    if any(marker in combined for marker in unavailable_markers):
        return _structured_browser_error(
            "当前内容已经失效或暂时不可浏览，请换一条公开内容再试。",
            reason_code="source_not_found",
            retryable=False,
        )
    if any(marker in combined for marker in challenge_markers):
        return _structured_browser_error(
            "当前页面触发了平台验证，请刷新项目级浏览器会话后再试。",
            reason_code="browser_challenge_required",
            retryable=True,
        )
    if any(marker in combined for marker in session_markers):
        return _structured_browser_error(
            "当前页面需要有效浏览器会话，请刷新项目级会话后再试。",
            reason_code="browser_session_expired",
            retryable=True,
        )
    if any(marker in combined for marker in missing_markers):
        return _structured_browser_error(
            "当前页面已经失效或不可访问，请换一条公开内容再试。",
            reason_code="source_not_found",
            retryable=False,
        )
    if any(marker in combined for marker in restricted_markers):
        return _structured_browser_error(
            "当前页面受访问限制，暂时无法通过浏览器会话提取。",
            reason_code="source_access_restricted",
            retryable=False,
        )
    return None


def _normalize_browser_exception(exc: Exception, *, timeout_message: str = "浏览器会话超时，请稍后重试。") -> BrowserProviderError:
    if isinstance(exc, BrowserProviderError):
        return exc
    if isinstance(exc, httpx.HTTPError):
        return _classify_http_error(exc)
    if isinstance(exc, PlaywrightTimeoutError):
        return _structured_browser_error(timeout_message, reason_code="browser_timeout", retryable=True)

    message = str(exc)
    lowered = message.lower()
    if "executable doesn't exist" in lowered or "playwright install" in lowered:
        return _structured_browser_error(
            "项目级浏览器运行时未就绪，请先安装 Playwright Chromium。",
            reason_code="browser_runtime_missing",
            retryable=False,
        )
    if "target page, context or browser has been closed" in lowered:
        return _structured_browser_error(
            "浏览器会话提前关闭，请稍后再试。",
            reason_code="browser_request_failed",
            retryable=True,
        )
    if any(
        marker in lowered
        for marker in (
            "net::err_",
            "connection reset",
            "connection refused",
            "connection aborted",
            "unexpected eof",
            "eof occurred",
            "remote protocol",
            "ssl",
            "broken pipe",
        )
    ):
        return _structured_browser_error(
            "来源站点的网络连接暂时不稳定，已自动尝试恢复。",
            reason_code="browser_network_failed",
            retryable=True,
        )
    return _structured_browser_error(
        "浏览器会话暂时没有稳定拿到页面结果，已自动尝试备用路径。",
        reason_code="browser_provider_failed",
        retryable=True,
    )


def _direct_media_url(candidate: str) -> str:
    value = (candidate or "").strip()
    if value.startswith("blob:"):
        return ""
    if not value.startswith(("http://", "https://")):
        return ""
    return _prefer_https(value)


def _looks_like_audio_stream(url: str) -> bool:
    lowered = (url or "").lower()
    return "media-audio" in lowered or "audio-und" in lowered


def _extract_background_image_url(value: str) -> str:
    match = re.search(r'url\(["\']?(https?://[^)"\']+)["\']?\)', value or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _upgrade_wechat_image_url(url: str) -> str:
    normalized = _prefer_https((url or "").strip())
    match = re.match(r"^(https?://[^?]+?)/\d+(\?.*)?$", normalized)
    if not match:
        return normalized
    return f"{match.group(1)}/0{match.group(2) or ''}"


def _is_wechat_noise_image(candidate: str, marker: str) -> bool:
    lowered = (candidate or "").lower()
    marker_text = (marker or "").lower()
    if not lowered.startswith(("http://", "https://")):
        return True
    if not any(token in lowered for token in ("mmbiz", "qpic.cn")):
        return True
    if "qlogo" in lowered:
        return True
    if any(token in lowered for token in ("avatar", "reward", "qrcode", "follow_avatar", "jump_author_avatar")):
        return True
    if any(token in marker_text for token in ("avatar", "reward", "qrcode", "follow_avatar", "jump_author_avatar")):
        return True
    return _is_noise_image(candidate, marker)


def _wechat_image_dedupe_key(url: str) -> str:
    normalized = _upgrade_wechat_image_url(url)
    parsed = urlparse(normalized)
    return f"{parsed.netloc.lower()}{parsed.path}"


def _pick_preferred_media_url(candidates: list[str]) -> str:
    direct_candidates = [_direct_media_url(item) for item in candidates]
    direct_candidates = [item for item in direct_candidates if item]
    if not direct_candidates:
        return ""
    for item in direct_candidates:
        if not _looks_like_audio_stream(item):
            return item
    return direct_candidates[0]


def _douyin_urls_from_address(address: object) -> list[str]:
    if not isinstance(address, dict):
        return []
    urls: list[str] = []
    for value in address.get("url_list") or []:
        if isinstance(value, str) and value:
            urls.append(value)
    uri = address.get("uri")
    if isinstance(uri, str) and uri:
        urls.append(f"https://www.douyin.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0")
    return urls


def _douyin_media_urls_from_detail(detail: object) -> list[str]:
    if not isinstance(detail, dict):
        return []
    video = detail.get("video") or {}
    if not isinstance(video, dict):
        return []

    candidates: list[str] = []
    for key in ("play_addr_h264", "play_addr", "play_addr_265", "download_addr", "download_suffix_logo_addr"):
        for item in _douyin_urls_from_address(video.get(key)):
            if item not in candidates:
                candidates.append(item)

    for item in video.get("bit_rate") or []:
        if not isinstance(item, dict):
            continue
        for value in _douyin_urls_from_address(item.get("play_addr")):
            if value not in candidates:
                candidates.append(value)
    return candidates


def _douyin_image_urls_from_detail(detail: object) -> list[str]:
    if not isinstance(detail, dict):
        return []
    images: list[str] = []
    for image_info in detail.get("images") or []:
        if not isinstance(image_info, dict):
            continue
        for key in ("url_list", "download_url_list", "origin_url"):
            candidates = image_info.get(key) or []
            if isinstance(candidates, list):
                for candidate in candidates:
                    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                        images.append(_prefer_https(candidate))
    for image_info in detail.get("image_infos") or []:
        if not isinstance(image_info, dict):
            continue
        for key in ("url_list", "download_url_list", "origin_url"):
            candidates = image_info.get(key) or []
            if isinstance(candidates, list):
                for candidate in candidates:
                    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                        images.append(_prefer_https(candidate))
    return list(dict.fromkeys(images))

def _douyin_live_photo_urls_from_detail(detail: object) -> list[str]:
    if not isinstance(detail, dict):
        return []
    live_urls: list[str] = []
    for image_info in detail.get("images") or detail.get("image_infos") or []:
        if not isinstance(image_info, dict):
            continue
        for key in ("live_photo_url", "motion_photo_url", "video_url"):
            value = image_info.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                live_urls.append(_prefer_https(value))
    return list(dict.fromkeys(live_urls))


def _format_mmss(seconds: int) -> str:
    minutes, remaining = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


def _douyin_duration_seconds(detail: dict) -> float | None:
    video = detail.get("video") or {}
    value = video.get("duration") or detail.get("duration")
    if not value:
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if duration > 3600:
        duration /= 1000.0
    return duration


def _normalize_douyin_timestamp(value: object, *, duration_seconds: float | None = None) -> int | None:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return None
    if timestamp < 0:
        return None

    candidates = [int(round(timestamp))]
    if timestamp > 3600:
        candidates.append(int(round(timestamp / 1000.0)))
    if timestamp > 3600000:
        candidates.append(int(round(timestamp / 1000000.0)))

    seen: set[int] = set()
    ordered_candidates: list[int] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered_candidates.append(candidate)

    if duration_seconds and duration_seconds > 0:
        max_allowed = max(int(round(duration_seconds)) + 3, 6)
        for candidate in ordered_candidates:
            if 0 <= candidate <= max_allowed:
                return candidate
        return None

    for candidate in ordered_candidates:
        if candidate <= 1800:
            return candidate
    return None


def _douyin_chapter_text(detail: object) -> str:
    if not isinstance(detail, dict):
        return ""
    chapter_list = detail.get("chapter_list") or []
    if not isinstance(chapter_list, list) or len(chapter_list) < 3:
        return ""

    duration_seconds = _douyin_duration_seconds(detail)
    lines: list[str] = []
    for item in chapter_list:
        if not isinstance(item, dict):
            continue
        desc = _normalize_text(str(item.get("desc") or ""))
        if not desc:
            continue
        timestamp = _normalize_douyin_timestamp(item.get("timestamp"), duration_seconds=duration_seconds)
        if timestamp is None:
            return ""
        line = f"{_format_mmss(timestamp)} {desc}".strip()
        if line not in lines:
            lines.append(line)
    if len(lines) < 3:
        return ""
    return "\n".join(lines)


def _settle_page(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=BROWSER_NETWORK_IDLE_TIMEOUT_MS)
    except Exception:
        pass
    page.wait_for_timeout(BROWSER_SETTLE_WAIT_MS)


def _classify_http_error(exc: httpx.HTTPError, *, timeout_reason: BrowserFailureCode = "browser_timeout") -> BrowserProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return _structured_browser_error("页面请求超时，请稍后重试。", reason_code=timeout_reason, retryable=True)

    response = getattr(exc, "response", None)
    if response is not None:
        if response.status_code == 404:
            return _structured_browser_error("当前页面已经失效或不可访问。", reason_code="source_not_found", retryable=False)
        if response.status_code in {401, 403}:
            return _structured_browser_error(
                "当前页面受访问限制，暂时无法直接请求成功。",
                reason_code="source_access_restricted",
                retryable=False,
            )
        if response.status_code == 429:
            return _structured_browser_error(
                "来源站点当前请求较多，已自动稍后重试。",
                reason_code="browser_rate_limited",
                retryable=True,
            )
        if response.status_code in {500, 502, 503, 504}:
            return _structured_browser_error(
                "来源站点暂时没有稳定响应，已自动尝试恢复。",
                reason_code="browser_server_unavailable",
                retryable=True,
            )

    lowered = str(exc).lower()
    if isinstance(exc, httpx.NetworkError) or any(
        marker in lowered
        for marker in (
            "connection reset",
            "connection refused",
            "connection aborted",
            "remote protocol",
            "unexpected eof",
            "eof occurred",
            "name or service not known",
            "temporary failure in name resolution",
            "getaddrinfo failed",
            "ssl",
            "broken pipe",
        )
    ):
        return _structured_browser_error(
            "来源站点的网络连接暂时不稳定，已自动尝试恢复。",
            reason_code="browser_network_failed",
            retryable=True,
        )

    return _structured_browser_error(
        "来源页面暂时没有稳定响应，已自动尝试恢复。",
        reason_code="browser_request_failed",
        retryable=True,
    )


@contextmanager
def _launch_context():
    with _browser_runtime.lease() as context:
        yield context


def _page_http_status_error(status: int | None) -> BrowserProviderError | None:
    """Map a browser navigation HTTP status to a safe, retryable error."""
    if status == 404:
        return _structured_browser_error(
            "当前页面已经失效或不可访问。",
            reason_code="source_not_found",
            retryable=False,
        )
    if status in {401, 403}:
        return _structured_browser_error(
            "当前页面受访问限制，暂时无法直接请求成功。",
            reason_code="source_access_restricted",
            retryable=False,
        )
    if status is not None and status >= 400:
        return _structured_browser_error(
            "来源页面暂时没有稳定响应，请稍后重试。",
            reason_code="browser_request_failed",
            retryable=True,
        )
    return None


def _capture_page(
    url: str,
    *,
    check_state: bool = True,
) -> tuple[str, str, str, str, list[str]]:
    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_GOTO_TIMEOUT_MS)
            response_status = getattr(response, "status", None)
            try:
                response_status = int(response_status) if response_status is not None else None
            except (TypeError, ValueError):
                response_status = None
            status_error = _page_http_status_error(response_status)
            if status_error is not None:
                raise status_error
            _settle_page(page)
            final_url = page.url
            title = page.title()

            media_url = ""
            try:
                media_url = page.evaluate(
                    """
                    () => {
                      const elements = Array.from(document.querySelectorAll('video, audio, source'));
                      for (const element of elements) {
                        const candidates = [
                          element.currentSrc,
                          element.src,
                          element.getAttribute('src'),
                          element.getAttribute('data-src'),
                        ];
                        for (const candidate of candidates) {
                          if (typeof candidate === 'string' && /^https?:\\/\\//i.test(candidate)) {
                            return candidate;
                          }
                        }
                      }
                      return '';
                    }
                    """
                ) or ""
            except Exception:
                media_url = ""

            try:
                body_text = page.evaluate(
                    """
                    () => {
                      const selectors = [
                        'article',
                        'main',
                        '[role="main"]',
                        '.note-content',
                        '.note-scroller',
                        '.desc',
                        '.video-info-detail',
                        '.content',
                      ];
                      let best = '';
                      for (const selector of selectors) {
                        for (const node of Array.from(document.querySelectorAll(selector))) {
                          const text = (node.innerText || '').trim();
                          if (text.length > best.length) best = text;
                        }
                      }
                      if (!best) {
                        best = (document.body?.innerText || '').trim();
                      }
                      return best;
                    }
                    """
                )
            except Exception:
                body_text = ""

            image_urls: list[str] = []
            if not _direct_media_url(media_url):
                try:
                    image_urls = page.evaluate(
                        """
                        () => {
                          const values = [];
                          const seen = new Set();
                          const candidates = Array.from(document.querySelectorAll('img, video[poster], source[srcset]'));
                          for (const el of candidates) {
                            const candidate =
                              el.getAttribute('data-src') ||
                              el.getAttribute('data-original') ||
                              el.getAttribute('data-lazy-src') ||
                              el.getAttribute('src') ||
                              el.getAttribute('poster') ||
                              el.getAttribute('srcset') ||
                              '';
                            if (!candidate || !/^https?:\\/\\//i.test(candidate)) continue;

                            const rect = el.getBoundingClientRect();
                            const width = el.naturalWidth || rect.width || 0;
                            const height = el.naturalHeight || rect.height || 0;
                            if (width < 120 || height < 120) continue;

                            const marker = [
                              el.getAttribute('alt') || '',
                              el.className || '',
                              el.id || '',
                              el.closest('a,button,[role=\"button\"]') ? 'interactive' : '',
                            ].join(' ').toLowerCase();
                            if (/(avatar|emoji|icon|logo|badge|sprite|interactive)/i.test(marker)) continue;
                            if (/fe-platform|favicon/i.test(candidate)) continue;

                            if (seen.has(candidate)) continue;
                            seen.add(candidate);
                            values.push({ url: candidate, top: rect.top, area: width * height, marker });
                          }

                          values.sort((a, b) => a.top - b.top || b.area - a.area);
                          return values.slice(0, 20).map((item) => ({ url: item.url, marker: item.marker }));
                        }
                        """
                    )
                except Exception:
                    image_urls = []

            filtered_images = [
                item["url"]
                for item in image_urls
                if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and not _is_noise_image(item["url"], str(item.get("marker") or ""))
            ]
            if check_state:
                state_error = _page_state_error(final_url, title, body_text)
                if state_error is not None:
                    raise state_error
            return final_url, title, media_url, _normalize_text(body_text), filtered_images
    except Exception as exc:  # noqa: BLE001
        raise _normalize_browser_exception(exc, timeout_message="浏览器打开页面超时，请稍后重试。") from exc


def fetch_generic_page(url: str) -> BrowserMediaResult:
    """Read a public webpage through the shared browser runtime.

    The HTTP extractor remains the fast path.  This bounded browser fallback
    is for pages that reject the plain client or only render their content in
    JavaScript; it deliberately reuses the same persistent runtime as the
    platform adapters instead of starting a second browser per request.
    """
    # Generic pages may legitimately contain words such as "login" or
    # "not found" in their article copy.  Platform state markers are only
    # meaningful for adapters that know the platform's error screens; the
    # navigation HTTP status above still rejects an actual 4xx/5xx response.
    final_url, title, media_url, body_text, image_urls = _capture_page(url, check_state=False)
    if not body_text and not image_urls and not media_url:
        raise BrowserProviderError(
            "网页已打开，但没有拿到可用正文或媒体地址。",
            reason_code="browser_request_failed",
            retryable=True,
        )
    return BrowserMediaResult(
        final_url=final_url,
        title=title or final_url,
        media_url=media_url,
        body_text=body_text,
        image_urls=image_urls,
        live_photo_video_urls=[],
    )


def _extract_xiaohongshu_state(html: str) -> dict | None:
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>", html, re.DOTALL)
    if not match:
        return None

    payload = match.group(1).strip()
    if payload.endswith(";"):
        payload = payload[:-1].strip()
    payload = re.sub(r"\bundefined\b", "null", payload)
    payload = re.sub(r"\bNaN\b", "null", payload)
    payload = re.sub(r"\bInfinity\b", "null", payload)

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _best_xiaohongshu_image_url(image: dict) -> str:
    candidates: list[str] = []
    for key in ("urlDefault", "urlPre", "url"):
        value = image.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    for item in image.get("infoList") or []:
        url = item.get("url")
        if isinstance(url, str) and url.strip():
            candidates.append(url.strip())

    for candidate in candidates:
        if candidate.startswith(("http://", "https://")):
            return _prefer_https(candidate)
    return ""


def _best_xiaohongshu_video_url(video: dict) -> str:
    media = video.get("media") or {}
    stream = media.get("stream") or {}

    for codec in ("h264", "h265"):
        for item in stream.get(codec) or []:
            for key in ("masterUrl", "avgBitrate"):
                value = item.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return _prefer_https(value)
            for candidate in item.get("backupUrls") or item.get("backupUrl") or []:
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                    return _prefer_https(candidate)
    return ""


def _best_xiaohongshu_live_photo_url(image: dict) -> str:
    media = image.get("media") or {}
    if isinstance(media, dict):
        for key in ("livePhotoUrl", "motionPhotoUrl", "videoUrl", "streamUrl", "url"):
            value = media.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                lowered = value.lower()
                if any(ext in lowered for ext in (".mp4", ".mov", ".m4v", "live", "video", "motion")):
                    return _prefer_https(value)

    candidates: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered_key = str(key).lower()
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    lowered_value = value.lower()
                    if (
                        ".mp4" in lowered_value
                        or ".mov" in lowered_value
                        or ".m4v" in lowered_value
                        or "live" in lowered_key
                        or "video" in lowered_key
                        or "motion" in lowered_key
                    ):
                        candidates.append(_prefer_https(value))
                else:
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(image)
    for candidate in candidates:
        if candidate.startswith(("http://", "https://")):
            return candidate
    return ""


def _extract_xiaohongshu_note_payload(html: str) -> dict | None:
    state = _extract_xiaohongshu_state(html)
    if not isinstance(state, dict):
        return None

    desktop = state.get("note")
    note_map = desktop.get("noteDetailMap") if isinstance(desktop, dict) else {}
    if not isinstance(note_map, dict):
        note_map = {}

    selected_note_id = ""
    selected_note: dict | None = None
    for note_id, wrapper in note_map.items():
        note = wrapper.get("note") if isinstance(wrapper, dict) else None
        if not isinstance(note, dict):
            continue
        if note.get("title") or note.get("desc") or note.get("imageList") or note.get("video"):
            selected_note_id = str(note_id)
            selected_note = note
            break

    if not selected_note:
        # Anonymous H5 shares use a different state tree from PC pages.
        mobile = state.get("noteData")
        data = mobile.get("data") if isinstance(mobile, dict) else None
        note = data.get("noteData") if isinstance(data, dict) else None
        if isinstance(note, dict) and note.get("noteId") and any(
            note.get(key) for key in ("title", "desc", "imageList", "video")
        ):
            selected_note_id = str(note["noteId"])
            selected_note = note
    if not selected_note:
        return None

    image_urls: list[str] = []
    live_photo_video_urls: list[str] = []
    seen: set[str] = set()
    for image in selected_note.get("imageList") or []:
        if not isinstance(image, dict):
            continue
        url = _best_xiaohongshu_image_url(image)
        if url and url not in seen:
            seen.add(url)
            image_urls.append(url)
            live_photo_video_urls.append(_best_xiaohongshu_live_photo_url(image))

    return {
        "note_id": selected_note_id,
        "title": str(selected_note.get("title") or "").strip(),
        "desc": _normalize_text(str(selected_note.get("desc") or "")),
        "type": str(selected_note.get("type") or "").strip().lower(),
        "image_urls": image_urls,
        "live_photo_video_urls": live_photo_video_urls,
        "video_url": _best_xiaohongshu_video_url(selected_note.get("video") or {}),
    }


def _normalize_xiaohongshu_url_candidate(candidate: str, *, base_url: str = "") -> str:
    value = unquote(unescape(str(candidate or "")).strip().strip("\"'"))
    value = value.replace(r"\u002F", "/").replace(r"\/", "/")
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/"):
        parsed_base = urlparse(base_url or "")
        base_host = (parsed_base.hostname or "").lower().rstrip(".")
        safe_base = (
            base_url
            if base_host == "xiaohongshu.com" or base_host.endswith(".xiaohongshu.com")
            else "https://www.xiaohongshu.com/"
        )
        value = urljoin(safe_base, value)
    value = _prefer_https(value)
    return value if _looks_like_xiaohongshu_full_url(value) else ""


def _extract_xiaohongshu_full_url_from_html(html: str, *, base_url: str = "") -> str:
    decoded_html = unquote(unescape(str(html or ""))).replace(r"\u002F", "/").replace(r"\/", "/")
    patterns = (
        r"https://(?:www\.|m\.)?xiaohongshu\.com/explore/[^\"'\s<]+",
        r"https://(?:www\.|m\.)?xiaohongshu\.com/discovery/item/[^\"'\s<]+",
        r"https://(?:www\.|m\.)?xiaohongshu\.com/note/[^\"'\s<]+",
        r"(?:/)(?:explore|discovery/item|note)/[^\"'\s<]+",
    )
    for pattern in patterns:
        matches = re.findall(pattern, decoded_html, re.IGNORECASE)
        for match in matches:
            cleaned = _normalize_xiaohongshu_url_candidate(match, base_url=base_url)
            if cleaned:
                return cleaned
    return ""


def _extract_html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


CN_PLATFORMS_BROWSER = {"xiaohongshu", "douyin", "wechat_article", "bilibili"}

_RELAY_ROUTE_PATTERNS = {
    "xiaohongshu": "**/*xiaohongshu.com/**",
    "douyin": "**/*.douyin.com/**",
}


def _setup_playwright_relay_routing(page, platform: str) -> None:
    """Intercept requests to CN platform domains and route through the Worker relay."""
    import urllib.parse

    pattern = _RELAY_ROUTE_PATTERNS.get(platform)
    if not pattern:
        return

    def _relay_route(route):
        try:
            relay_url = f"{CN_PLATFORM_RELAY_URL}?url={urllib.parse.quote(route.request.url, safe='')}"
            with httpx.Client(
                timeout=DEFAULT_TIMEOUT_SECONDS * 2,
                trust_env=False,
                headers={"User-Agent": DEFAULT_USER_AGENT},
            ) as client:
                resp = client.get(relay_url)
                safe_headers = {"Content-Type": resp.headers.get("Content-Type", "text/html; charset=utf-8")}
                if "Set-Cookie" in resp.headers:
                    safe_headers["Set-Cookie"] = resp.headers["Set-Cookie"]
                route.fulfill(status=resp.status_code, headers=safe_headers, body=resp.content)
        except Exception:
            route.abort()

    page.route(pattern, _relay_route)


def _browser_proxy_for_platform(platform: str) -> str | None:
    if CN_PLATFORM_PROXY and platform in CN_PLATFORMS_BROWSER:
        return CN_PLATFORM_PROXY
    return None


def _browser_needs_relay(platform: str) -> bool:
    if CN_PLATFORM_PROXY or platform not in CN_PLATFORMS_BROWSER:
        return False
    if DEPLOYMENT_MODE == "local":
        return False
    return True


def _relay_fetch_html(url: str) -> tuple[str, str, str]:
    """Fetch a URL through the Cloudflare Worker relay, returning (final_url, title, html)."""
    import urllib.parse
    relay_url = f"{CN_PLATFORM_RELAY_URL}?url={urllib.parse.quote(url, safe='')}"
    try:
        with httpx.Client(
            follow_redirects=False,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=DEFAULT_TIMEOUT_SECONDS * 2,
            trust_env=False,
        ) as client:
            response = client.get(relay_url)
            response.raise_for_status()
            html = response.text
            final_url = response.headers.get("X-Proxied-Url", url)
            return final_url, _extract_html_title(html), html
    except httpx.HTTPError as exc:
        raise _classify_http_error(exc) from exc


def _fetch_xiaohongshu_html(url: str) -> tuple[str, str, str]:
    if _browser_needs_relay("xiaohongshu"):
        return _relay_fetch_html(url)
    mobile_agent = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 "
        "Mobile/15E148 Safari/604.1"
    )
    agents = [DEFAULT_USER_AGENT, mobile_agent]
    if urlparse(url).path.startswith("/discovery/item/"):
        agents.reverse()
    deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS * 2
    first_result: tuple[str, str, str] | None = None
    proxy = _browser_proxy_for_platform("xiaohongshu")
    for agent in agents:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        client_kwargs = {
            "follow_redirects": True,
            "headers": {"User-Agent": agent},
            "timeout": min(DEFAULT_TIMEOUT_SECONDS, remaining),
            "trust_env": False,
        }
        if proxy:
            client_kwargs["proxy"] = proxy
        try:
            with httpx.Client(**client_kwargs) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if time.monotonic() >= deadline:
                            raise httpx.ReadTimeout("Source read budget exceeded")
                        if size > 8 * 1024 * 1024:
                            raise _structured_browser_error(
                                "来源页面过大，暂时无法读取。",
                                reason_code="browser_request_failed", retryable=False,
                            )
                        chunks.append(chunk)
                    if time.monotonic() >= deadline:
                        raise httpx.ReadTimeout("Source read budget exceeded")
                    html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                    result = str(response.url), _extract_html_title(html), html
        except httpx.HTTPError as exc:
            if first_result is None:
                raise _classify_http_error(exc) from exc
            break
        if _extract_xiaohongshu_note_payload(result[2]):
            return result
        if first_result is None:
            first_result = result
    # Preserve the primary response for existing structured error handling.
    if first_result is not None:
        return first_result
    raise _structured_browser_error(
        "读取来源超时，请稍后重试。", reason_code="browser_timeout", retryable=True,
    )


def _fetch_xiaohongshu_relay_fallback(url: str) -> tuple[str, str, str]:
    """Use the configured edge relay only after the local public path misses.

    Local runs should stay direct and fast for the normal case.  A relay is a
    recovery path for platforms that challenge the local IP while still
    serving public SSR data from another edge location.
    """
    if not CN_PLATFORM_RELAY_URL:
        raise _structured_browser_error(
            "当前没有可用的平台恢复链路。",
            reason_code="browser_request_failed",
            retryable=True,
        )
    return _relay_fetch_html(url)


def resolve_xiaohongshu_share_url(url: str) -> str:
    try:
        final_url, _, html = _fetch_xiaohongshu_html(url)
        if _looks_like_xiaohongshu_full_url(final_url):
            return final_url
        extracted = _extract_xiaohongshu_full_url_from_html(html, base_url=final_url or url)
        if extracted:
            return extracted
        if _is_xiaohongshu_note_less_landing(final_url):
            return ""
    except BrowserProviderError:
        pass

    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            observed_urls: set[str] = set()

            def remember(candidate: str) -> None:
                if isinstance(candidate, str) and _looks_like_xiaohongshu_full_url(candidate):
                    observed_urls.add(_prefer_https(candidate))

            if _browser_needs_relay("xiaohongshu"):
                _setup_playwright_relay_routing(page, "xiaohongshu")

            page.on("request", lambda request: remember(request.url))
            page.on("response", lambda response: remember(response.url))
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_GOTO_TIMEOUT_MS)
            _settle_page(page)
            final_url = page.url
            if _looks_like_xiaohongshu_full_url(final_url):
                return final_url

            html = page.content()
            extracted = _extract_xiaohongshu_full_url_from_html(html, base_url=final_url or url)
            if extracted:
                return extracted

            hrefs = page.eval_on_selector_all(
                "a[href]",
                """
                nodes => nodes
                  .map(node => node.getAttribute('href') || '')
                  .filter(Boolean)
                """,
            )
            for href in hrefs:
                if isinstance(href, str):
                    candidate = _normalize_xiaohongshu_url_candidate(href, base_url=final_url or url)
                    if candidate:
                        return candidate

            for candidate in observed_urls:
                if _looks_like_xiaohongshu_full_url(candidate):
                    return candidate
    except Exception:
        return ""

    return ""


def fetch_douyin_media(
    url: str,
    *,
    goto_timeout_ms: int = BROWSER_GOTO_TIMEOUT_MS,
) -> BrowserMediaResult:
    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            if _browser_needs_relay("douyin"):
                _setup_playwright_relay_routing(page, "douyin")
            observed_media_urls: list[str] = []
            observed_detail_urls: list[str] = []
            aweme_detail: dict | None = None

            def _remember_media(candidate: str) -> None:
                direct = _direct_media_url(candidate)
                if direct and direct not in observed_media_urls:
                    observed_media_urls.append(direct)

            def _capture_response(response) -> None:
                nonlocal aweme_detail
                try:
                    if aweme_detail is None and "/aweme/v1/web/aweme/detail/" in response.url:
                        if response.url not in observed_detail_urls:
                            observed_detail_urls.append(response.url)
                        payload = response.json()
                        detail = payload.get("aweme_detail") if isinstance(payload, dict) else None
                        if isinstance(detail, dict):
                            aweme_detail = detail
                    if "douyinvod.com/" in response.url or "mime_type=video_mp4" in response.url:
                        _remember_media(response.url)
                except Exception:
                    return

            page.on("response", _capture_response)
            page.goto(url, wait_until="domcontentloaded", timeout=max(1_000, int(goto_timeout_ms)))
            _settle_page(page)
            page.wait_for_timeout(2200)

            try:
                resource_urls = page.evaluate(
                    """
                    () => performance
                      .getEntriesByType('resource')
                      .map((entry) => entry.name || '')
                      .filter(Boolean)
                    """
                )
            except Exception:
                resource_urls = []
            if isinstance(resource_urls, list):
                for resource_url in resource_urls:
                    if not isinstance(resource_url, str):
                        continue
                    if "/aweme/v1/web/aweme/detail/" in resource_url and resource_url not in observed_detail_urls:
                        observed_detail_urls.append(resource_url)
                    if "douyinvod.com/" in resource_url or "mime_type=video_mp4" in resource_url:
                        _remember_media(resource_url)

            if aweme_detail is None and observed_detail_urls:
                try:
                    payload = page.evaluate(
                        """
                        async (detailUrl) => {
                          const response = await fetch(detailUrl, {
                            credentials: 'include',
                            headers: { 'accept': 'application/json, text/plain, */*' },
                          });
                          return await response.json();
                        }
                        """,
                        observed_detail_urls[0],
                    )
                    detail = payload.get("aweme_detail") if isinstance(payload, dict) else None
                    if isinstance(detail, dict):
                        aweme_detail = detail
                except Exception:
                    pass

            final_url = page.url
            title = page.title()
            media_url = ""
            try:
                if page.locator("video").count():
                    media_url = page.eval_on_selector("video", "el => el.currentSrc || el.src || ''")
            except Exception:
                media_url = ""

            try:
                body_text = page.evaluate(
                    """
                    () => {
                      const selectors = [
                        '.desc',
                        '.video-info-detail',
                        'article',
                        'main',
                        '[role="main"]',
                        '.content',
                      ];
                      let best = '';
                      for (const selector of selectors) {
                        for (const node of Array.from(document.querySelectorAll(selector))) {
                          const text = (node.innerText || '').trim();
                          if (text.length > best.length) best = text;
                        }
                      }
                      if (!best) {
                        best = (document.body?.innerText || '').trim();
                      }
                      return best;
                    }
                    """
                )
            except Exception:
                body_text = ""

            detail_media_url = _pick_preferred_media_url(_douyin_media_urls_from_detail(aweme_detail))
            network_media_url = _pick_preferred_media_url(observed_media_urls)
            media_url = detail_media_url or network_media_url or _direct_media_url(media_url)

            image_urls: list[str] = []
            live_photo_video_urls: list[str] = []
            if aweme_detail:
                chapter_text = _douyin_chapter_text(aweme_detail)
                detail_text = _normalize_text(str(aweme_detail.get("desc") or ""))
                if detail_text:
                    body_text = detail_text
                if not media_url:
                    image_urls = _douyin_image_urls_from_detail(aweme_detail)
                    live_photo_video_urls = _douyin_live_photo_urls_from_detail(aweme_detail)

            state_error = _page_state_error(final_url, title, body_text)

            if state_error is not None and not (aweme_detail or media_url or image_urls):
                raise state_error

            if not media_url and not image_urls:
                raise BrowserProviderError(
                    "抖音页面已加载，但没有拿到可用视频或图片地址。",
                    reason_code="browser_request_failed",
                    retryable=True,
                )
            cleaned_title = re.sub(r"\s*-\s*抖音\s*$", "", title or "").strip()
            return BrowserMediaResult(
                final_url=final_url,
                title=cleaned_title or "抖音内容",
                media_url=media_url,
                body_text=_normalize_text(body_text),
                image_urls=image_urls,
                live_photo_video_urls=live_photo_video_urls,
            )
    except Exception as exc:  # noqa: BLE001
        raise _normalize_browser_exception(exc, timeout_message="浏览器打开页面超时，请稍后重试。") from exc


def fetch_wechat_page(url: str) -> BrowserMediaResult:
    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_GOTO_TIMEOUT_MS)
            _settle_page(page)

            final_url = page.url
            payload = page.evaluate(
                """
                () => {
                  const firstText = (selectors) => {
                    for (const selector of selectors) {
                      const node = document.querySelector(selector);
                      const text = (node?.innerText || '').trim();
                      if (text) return text;
                    }
                    return '';
                  };

                  const imageCandidates = [];
                  const seen = new Set();
                  const pushCandidate = (url, marker) => {
                    const value = (url || '').trim();
                    if (!value) return;
                    const key = `${value}|${marker || ''}`;
                    if (seen.has(key)) return;
                    seen.add(key);
                    imageCandidates.push({ url: value, marker: marker || '' });
                  };

                  for (const img of Array.from(document.images)) {
                    const url = img.getAttribute('data-src')
                      || img.getAttribute('data-original')
                      || img.getAttribute('data-lazy-src')
                      || img.getAttribute('src')
                      || '';
                    pushCandidate(url, `${img.className || ''} ${img.id || ''} ${img.alt || ''} ${img.title || ''}`);
                  }

                  for (const node of Array.from(document.querySelectorAll('body, body *'))) {
                    const bg = getComputedStyle(node).backgroundImage || '';
                    if (bg && bg !== 'none') {
                      pushCandidate(bg, `${node.className || ''} ${node.id || ''}`);
                    }
                  }

                  return {
                    title: firstText(['#js_content .rich_media_title', '#activity-name', 'h1']),
                    bodyText: firstText(['#js_image_desc', '#js_content', 'article', 'main']) || (document.body?.innerText || '').trim(),
                    imageCandidates,
                  };
                }
                """
            )

            title = str((payload or {}).get("title") or "").strip()
            body_text = _normalize_text(str((payload or {}).get("bodyText") or ""))
            raw_candidates = (payload or {}).get("imageCandidates") or []

            image_urls: list[str] = []
            seen_image_keys: set[str] = set()
            for item in raw_candidates:
                if not isinstance(item, dict):
                    continue
                candidate = str(item.get("url") or "").strip()
                marker = str(item.get("marker") or "")
                if candidate.startswith("url("):
                    candidate = _extract_background_image_url(candidate)
                candidate = _upgrade_wechat_image_url(candidate)
                if not candidate or _is_wechat_noise_image(candidate, marker):
                    continue
                dedupe_key = _wechat_image_dedupe_key(candidate)
                if dedupe_key in seen_image_keys:
                    continue
                seen_image_keys.add(dedupe_key)
                image_urls.append(candidate)

            state_error = _page_state_error(final_url, title, body_text)
            if state_error is not None and not (body_text or image_urls):
                raise state_error

            if not body_text and not image_urls:
                raise BrowserProviderError(
                    "微信公众号页面已加载，但没有拿到可用正文或图片地址。",
                    reason_code="browser_request_failed",
                    retryable=True,
                )

            return BrowserMediaResult(
                final_url=final_url,
                title=title or "微信公众号文章",
                media_url="",
                body_text=body_text,
                image_urls=image_urls,
                live_photo_video_urls=[],
            )
    except Exception as exc:  # noqa: BLE001
        raise _normalize_browser_exception(exc, timeout_message="浏览器打开微信公众号页面超时，请稍后重试。") from exc


def _fetch_xiaohongshu_page_once(url: str) -> BrowserMediaResult:
    """Extract Xiaohongshu note data.

    Strategy: try HTTP relay first (static HTML has SSR data for real notes),
    fall back to Playwright browser only if relay HTML lacks note data.
    """
    # Method 1: HTTP relay — static HTML has SSR __INITIAL_STATE__ with note data
    direct_error: BrowserProviderError | None = None
    try:
        final_url, page_title, html = _fetch_xiaohongshu_html(url)
        note_payload = _extract_xiaohongshu_note_payload(html)
        if note_payload:
            title = (note_payload["title"] or page_title or "").strip()
            return BrowserMediaResult(
                final_url=final_url,
                title=title or "Xiaohongshu content",
                media_url=_prefer_https((note_payload["video_url"] or "").strip()) if note_payload["video_url"] else "",
                body_text=_normalize_text(note_payload["desc"] or ""),
                image_urls=[_prefer_https(item) for item in note_payload["image_urls"]],
                live_photo_video_urls=[_prefer_https(item) if item else "" for item in note_payload.get("live_photo_video_urls") or []],
            )
        # The platform uses a successful HTTP response for its error shell.
        # Inspect the redirected URL before spending time on browser recovery.
        direct_error = _page_state_error(final_url, page_title, "")
        if direct_error is not None and direct_error.reason_code in {
            "source_not_found",
            "source_access_restricted",
        }:
            raise direct_error
    except BrowserProviderError as exc:
        direct_error = exc

    # If the direct path was challenged or otherwise returned no note data,
    # try the already-configured edge relay before opening another browser
    # page.  This is deliberately one bounded fallback, not a retry loop.
    if not _browser_needs_relay("xiaohongshu"):
        try:
            final_url, page_title, html = _fetch_xiaohongshu_relay_fallback(url)
            note_payload = _extract_xiaohongshu_note_payload(html)
            if note_payload:
                title = (note_payload["title"] or page_title or "").strip()
                return BrowserMediaResult(
                    final_url=final_url,
                    title=title or "Xiaohongshu content",
                    media_url=_prefer_https((note_payload["video_url"] or "").strip()) if note_payload["video_url"] else "",
                    body_text=_normalize_text(note_payload["desc"] or ""),
                    image_urls=[_prefer_https(item) for item in note_payload["image_urls"]],
                    live_photo_video_urls=[_prefer_https(item) if item else "" for item in note_payload.get("live_photo_video_urls") or []],
                )
            relay_error = _page_state_error(final_url, page_title, "")
            if relay_error is not None and relay_error.reason_code in {
                "source_not_found",
                "source_access_restricted",
            }:
                raise relay_error
        except BrowserProviderError as exc:
            if exc.reason_code in {"source_not_found", "source_access_restricted"}:
                raise
            # Keep the direct error for the browser path when the optional
            # relay is unavailable or times out.

    # Method 2: Playwright browser — fallback for SPA-only pages
    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()

            if _browser_needs_relay("xiaohongshu"):
                _setup_playwright_relay_routing(page, "xiaohongshu")

            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_GOTO_TIMEOUT_MS)
            _settle_page(page)

            early_body_text = ""
            try:
                early_body_text = page.locator("body").inner_text(timeout=2000)
            except Exception:
                pass
            early_state_error = _page_state_error(page.url, page.title(), early_body_text)
            if early_state_error is not None:
                raise early_state_error

            # Wait for SPA to populate note data in __INITIAL_STATE__
            try:
                page.wait_for_function(
                    """
                    () => {
                      const parseInitialState = (text) => {
                        const marker = 'window.__INITIAL_STATE__';
                        const start = text.indexOf(marker);
                        if (start < 0) return null;
                        const equals = text.indexOf('=', start + marker.length);
                        if (equals < 0) return null;
                        let payload = text.slice(equals + 1).trim();
                        const end = payload.lastIndexOf('}');
                        if (end >= 0) payload = payload.slice(0, end + 1);
                        try {
                          return JSON.parse(payload.replace(/undefined/g, 'null').replace(/NaN/g, 'null'));
                        } catch {
                          return null;
                        }
                      };
                      for (const el of document.querySelectorAll('script')) {
                        const s = parseInitialState(el.textContent || '');
                        if (!s) continue;
                        const notes = s?.note?.noteDetailMap || {};
                        for (const w of Object.values(notes)) {
                          const n = w?.note;
                          if (n && (n.title || n.desc || n.imageList?.length || n.video)) return true;
                        }
                      }
                      return false;
                    }
                    """,
                    timeout=BROWSER_GOTO_TIMEOUT_MS,
                )
            except Exception:
                pass  # page may still have content even if wait times out

            final_url = page.url
            page_title = page.title()

            # Extract note data directly via evaluate — no heavy HTML transfer
            raw = page.evaluate(
                """
                () => {
                  const parseInitialState = (text) => {
                    const marker = 'window.__INITIAL_STATE__';
                    const start = text.indexOf(marker);
                    if (start < 0) return null;
                    const equals = text.indexOf('=', start + marker.length);
                    if (equals < 0) return null;
                    let payload = text.slice(equals + 1).trim();
                    const end = payload.lastIndexOf('}');
                    if (end >= 0) payload = payload.slice(0, end + 1);
                    try {
                      return JSON.parse(payload.replace(/undefined/g, 'null').replace(/NaN/g, 'null'));
                    } catch {
                      return null;
                    }
                  };
                  const scripts = [...document.querySelectorAll('script')];
                  for (const s of scripts) {
                    try {
                      const state = parseInitialState(s.textContent || '');
                      if (!state) continue;
                      const notes = state?.note?.noteDetailMap || {};
                      for (const [, wrapper] of Object.entries(notes)) {
                        const note = wrapper?.note;
                        if (!note || !(note.title || note.desc || note.imageList?.length || note.video)) continue;
                        const images = [];
                        const livePhotos = [];
                        for (const img of note.imageList || []) {
                          const url = img?.urlDefault || img?.urlPre || img?.url || '';
                          if (url) images.push(url);
                          const live = img?.media?.livePhotoUrl || img?.media?.motionPhotoUrl || '';
                          livePhotos.push(live || '');
                        }
                        let videoUrl = '';
                        const stream = note.video?.media?.stream;
                        if (stream) {
                          for (const codec of ['h264', 'h265']) {
                            for (const item of stream[codec] || []) {
                              const u = item?.masterUrl || '';
                              if (u) { videoUrl = u; break; }
                            }
                            if (videoUrl) break;
                          }
                        }
                        return {
                          title: note.title || '',
                          desc: note.desc || '',
                          type: note.type || '',
                          images,
                          livePhotos,
                          videoUrl
                        };
                      }
                    } catch {}
                  }
                  return null;
                }
                """
            )

            # XHS currently hydrates noteDetailMap through a follow-up API call.
            # In that case the initial-state script exists but is empty while
            # the rendered note DOM already contains the title, description,
            # and primary images. Use that stable, user-visible surface as a
            # read-only fallback instead of treating a valid page as empty.
            if not raw:
                raw = page.evaluate(
                    """
                    () => {
                      const root = document.querySelector('#noteContainer, .note-container');
                      if (!root) return null;
                      const title = (root.querySelector('#detail-title, h1')?.textContent || '').trim();
                      const desc = (root.querySelector('#detail-desc, .desc')?.textContent || '').trim();
                      const images = [];
                      const seen = new Set();
                      for (const image of root.querySelectorAll('img')) {
                        const src = image.currentSrc || image.src || image.getAttribute('data-src') || '';
                        const width = image.naturalWidth || 0;
                        const height = image.naturalHeight || 0;
                        const mainImage = image.closest('.note-slider-img, .swiper-slide, .live-photo-contain');
                        const marker = `${image.className || ''} ${src}`;
                        if (!src || !mainImage || width < 200 || height < 200) continue;
                        if (/(avatar|emoji|icon|logo|comment\\/)/i.test(marker)) continue;
                        if (seen.has(src)) continue;
                        seen.add(src);
                        images.push(src);
                      }
                      const videoUrl = [...root.querySelectorAll('video')]
                        .map(video => video.currentSrc || video.src || '')
                        .find(src => /^https?:\\/\\//i.test(src)) || '';
                      if (!title && !desc && !images.length && !videoUrl) return null;
                      return {
                        title,
                        desc,
                        type: videoUrl ? 'video' : '',
                        images,
                        livePhotos: [],
                        videoUrl,
                      };
                    }
                    """
                )

            title = page_title
            body_text = ""
            media_url = ""
            image_urls: list[str] = []
            live_photo_video_urls: list[str] = []

            if raw:
                title = raw.get("title") or page_title
                body_text = raw.get("desc") or ""
                media_url = raw.get("videoUrl") or ""
                if raw.get("type") != "video":
                    image_urls = [u for u in raw.get("images") or [] if u]
                    live_photo_video_urls = [u for u in raw.get("livePhotos") or [] if u]
    except Exception as exc:
        raise _normalize_browser_exception(exc, timeout_message="浏览器打开小红书页面超时，请稍后重试。") from exc

    state_error = _page_state_error(final_url, title, body_text)
    if state_error is not None:
        raise state_error

    cleaned_title = re.sub(r"\s*-\s*小红书\s*$", "", title or "").strip()
    return BrowserMediaResult(
        final_url=final_url,
        title=cleaned_title or "小红书内容",
        media_url=_prefer_https(media_url.strip()) if media_url else "",
        body_text=_normalize_text(body_text),
        image_urls=[_prefer_https(item) for item in image_urls],
        live_photo_video_urls=[_prefer_https(item) if item else "" for item in live_photo_video_urls],
    )


def fetch_xiaohongshu_page(url: str) -> BrowserMediaResult:
    """Fetch a note and recycle the isolated session once after a challenge.

    A stale persistent context can retain a platform challenge even after the
    page is closed.  Retry once with a fresh context before surfacing the
    recoverable error; this keeps the normal fast path unchanged and avoids an
    unbounded retry loop when the platform is actively blocking the request.
    """
    try:
        return _fetch_xiaohongshu_page_once(url)
    except BrowserProviderError as exc:
        if exc.reason_code not in {"browser_challenge_required", "browser_session_expired"}:
            raise
        refresh_browser_runtime()
        return _fetch_xiaohongshu_page_once(url)

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.settings import BROWSER_PROFILE_DIR, CN_PLATFORM_PROXY, CN_PLATFORM_RELAY_URL, DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT, DEPLOYMENT_MODE


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
    "browser_challenge_required",
    "browser_session_expired",
    "source_not_found",
    "source_access_restricted",
    "browser_request_failed",
    "browser_provider_failed",
]


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
    lowered = (url or "").lower()
    return (
        lowered.startswith("https://www.xiaohongshu.com/")
        and any(token in lowered for token in ("/explore/", "/discovery/item/", "/note/"))
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
    return _structured_browser_error(
        f"浏览器会话提取失败：{message}",
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

    return _structured_browser_error(
        f"页面请求失败：{exc}",
        reason_code="browser_request_failed",
        retryable=True,
    )


@contextmanager
def _launch_context():
    with sync_playwright() as playwright:
        last_exc: Exception | None = None
        context = None
        base_options = {
            "user_data_dir": str(BROWSER_PROFILE_DIR),
            "headless": True,
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": {"width": 1360, "height": 920},
            "args": [
                "--no-sandbox",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
            ],
        }
        for channel in (None, "chrome", "msedge"):
            launch_options = dict(base_options)
            if channel:
                launch_options["channel"] = channel
            try:
                context = playwright.chromium.launch_persistent_context(**launch_options)
                break
            except Exception as exc:
                last_exc = exc
                continue

        if context is None:
            raise _normalize_browser_exception(last_exc or RuntimeError("browser launch failed"))

        try:
            yield context
        finally:
            context.close()


def _capture_page(url: str) -> tuple[str, str, str, str, list[str]]:
    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_GOTO_TIMEOUT_MS)
            _settle_page(page)
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
            state_error = _page_state_error(final_url, title, body_text)
            if state_error is not None:
                raise state_error
            return final_url, title, media_url, _normalize_text(body_text), filtered_images
    except Exception as exc:  # noqa: BLE001
        raise _normalize_browser_exception(exc, timeout_message="浏览器打开页面超时，请稍后重试。") from exc


def _extract_xiaohongshu_state(html: str) -> dict | None:
    match = re.search(r"window\.__INITIAL_STATE__=(.*?)</script>", html, re.DOTALL)
    if not match:
        return None

    payload = match.group(1).strip()
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
    if not state:
        return None

    note_map = ((state.get("note") or {}).get("noteDetailMap") or {})
    if not isinstance(note_map, dict):
        return None

    selected_note_id = ""
    selected_note: dict | None = None
    for note_id, wrapper in note_map.items():
        note = (wrapper or {}).get("note") or {}
        if not isinstance(note, dict):
            continue
        if note.get("title") or note.get("desc") or note.get("imageList") or note.get("video"):
            selected_note_id = str(note_id)
            selected_note = note
            break

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


def _extract_xiaohongshu_full_url_from_html(html: str) -> str:
    patterns = (
        r"https://www\.xiaohongshu\.com/explore/[^\"'\s<]+",
        r"https://www\.xiaohongshu\.com/discovery/item/[^\"'\s<]+",
        r"https://www\.xiaohongshu\.com/note/[^\"'\s<]+",
    )
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches:
            cleaned = _prefer_https(match.replace("\\u002F", "/").replace("&amp;", "&"))
            if _looks_like_xiaohongshu_full_url(cleaned):
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
    client_kwargs = {
        "follow_redirects": True,
        "headers": {"User-Agent": DEFAULT_USER_AGENT},
        "timeout": DEFAULT_TIMEOUT_SECONDS * 2,
        "trust_env": False,
    }
    proxy = _browser_proxy_for_platform("xiaohongshu")
    if proxy:
        client_kwargs["proxy"] = proxy
    try:
        with httpx.Client(**client_kwargs) as client:
            response = client.get(url)
            response.raise_for_status()
            return str(response.url), _extract_html_title(response.text), response.text
    except httpx.HTTPError as exc:
        raise _classify_http_error(exc) from exc


def resolve_xiaohongshu_share_url(url: str) -> str:
    try:
        final_url, _, html = _fetch_xiaohongshu_html(url)
        if _looks_like_xiaohongshu_full_url(final_url):
            return final_url
        extracted = _extract_xiaohongshu_full_url_from_html(html)
        if extracted:
            return extracted
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
            extracted = _extract_xiaohongshu_full_url_from_html(html)
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
                    candidate = _prefer_https(href.replace("\\u002F", "/").replace("&amp;", "&"))
                    if _looks_like_xiaohongshu_full_url(candidate):
                        return candidate

            for candidate in observed_urls:
                if _looks_like_xiaohongshu_full_url(candidate):
                    return candidate
    except Exception:
        return ""

    return ""


def fetch_douyin_media(url: str) -> BrowserMediaResult:
    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()
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
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_GOTO_TIMEOUT_MS)
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


def fetch_xiaohongshu_page(url: str) -> BrowserMediaResult:
    """Extract Xiaohongshu note data using browser (SPA requires JS rendering).

    Strategy: skip HTTP relay (static HTML from SPA has empty state),
    go straight to Playwright browser, extract note data directly from
    window.__INITIAL_STATE__ via page.evaluate() — much faster than
    transferring full page.content() HTML.
    """
    try:
        with _launch_context() as context:
            page = context.pages[0] if context.pages else context.new_page()

            if _browser_needs_relay("xiaohongshu"):
                _setup_playwright_relay_routing(page, "xiaohongshu")

            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_GOTO_TIMEOUT_MS)
            _settle_page(page)

            # Wait for SPA to populate note data in __INITIAL_STATE__
            try:
                page.wait_for_function(
                    """
                    () => {
                      const el = document.querySelector('script');
                      if (!el) return false;
                      const m = el.textContent?.match(/window\\.__INITIAL_STATE__\\s*=\\s*(.*?)<\\/script>/s);
                      if (!m) return false;
                      try {
                        const s = JSON.parse(m[1].replace(/undefined/g, 'null').replace(/NaN/g, 'null'));
                        const notes = s?.note?.noteDetailMap || {};
                        for (const w of Object.values(notes)) {
                          const n = w?.note;
                          if (n && (n.title || n.desc || n.imageList?.length || n.video)) return true;
                        }
                      } catch {}
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
                  const scripts = [...document.querySelectorAll('script')];
                  for (const s of scripts) {
                    const m = s.textContent?.match(/window\\.__INITIAL_STATE__\\s*=\\s*(.*?)<\\/script>/s);
                    if (!m) continue;
                    try {
                      const state = JSON.parse(m[1].replace(/undefined/g, 'null').replace(/NaN/g, 'null'));
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

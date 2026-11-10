"""Tell the person when a newer 万象成文 is out.

Platforms change their pages often, and an old copy simply stops working, so the
app checks once in a while and shows a quiet line on the home page. The check
reads a small manifest from the domestic mirror first, then GitHub Releases.
It never downloads or installs anything, sends nothing about the person, and
any failure just means no notice.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

import httpx

UPDATE_PAGE_URL = "https://qinxujunai.github.io/Audio2Text/"
MANIFEST_SOURCES = (
    "https://media-1416754078.cos.ap-shanghai.myqcloud.com/wanxiang/latest.json",
    "https://api.github.com/repos/qinxujunai/Audio2Text/releases/latest",
)
CACHE_SECONDS = 6 * 3600
TIMEOUT_SECONDS = 5.0
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "latest": None}


def parse_version(value: object) -> tuple[int, int, int] | None:
    match = _VERSION.match(str(value or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def _latest_from(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    # latest.json on the mirror uses "version"; GitHub Releases uses "tag_name".
    if payload.get("draft") or payload.get("prerelease"):
        return None
    for key in ("version", "tag_name"):
        version = parse_version(payload.get(key))
        if version is not None:
            return ".".join(str(part) for part in version)
    return None


def _fetch_latest(client: httpx.Client) -> str | None:
    for url in MANIFEST_SOURCES:
        try:
            response = client.get(url, headers={"Accept": "application/json", "User-Agent": "wanxiang-update-check"})
            if response.status_code != 200:
                continue
            latest = _latest_from(response.json())
            if latest:
                return latest
        except (httpx.HTTPError, ValueError):
            continue
    return None


def update_status(current_version: str, *, client: httpx.Client | None = None, now: float | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {"current": current_version, "latest": None, "update_available": False, "url": UPDATE_PAGE_URL}
    if os.environ.get("AUDIO2TEXT_UPDATE_CHECK", "1").strip().lower() in {"0", "false", "off", "no"}:
        return status
    current = parse_version(current_version)
    if current is None:
        return status
    moment = time.time() if now is None else now
    with _lock:
        cached = _cache["latest"] if moment - float(_cache["at"]) < CACHE_SECONDS else None
    latest = cached
    if latest is None:
        owned = client is None
        http = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
        try:
            latest = _fetch_latest(http)
        finally:
            if owned:
                http.close()
        if latest:
            with _lock:
                _cache.update(at=moment, latest=latest)
    newer = parse_version(latest)
    status["latest"] = latest
    status["update_available"] = bool(newer and newer > current)
    return status


def reset_cache() -> None:
    with _lock:
        _cache.update(at=0.0, latest=None)

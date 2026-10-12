"""Bounded yt-dlp media worker.

The API worker must never inherit a downloader that can wait indefinitely on a
stalled media endpoint.  This module is intentionally small and communicates
through one JSON request on stdin and one JSON result on stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from yt_dlp import YoutubeDL


def _compact_info(info: dict) -> dict:
    requested_formats = []
    for item in info.get("requested_formats") or []:
        if not isinstance(item, dict):
            continue
        requested_formats.append(
            {
                key: item.get(key)
                for key in ("format_id", "width", "height", "ext")
                if item.get(key) is not None
            }
        )
    return {
        "id": info.get("id"),
        "format_id": info.get("format_id"),
        "resolution": info.get("resolution"),
        "requested_formats": requested_formats,
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        capture_dir = Path(str(request["capture_dir"])).resolve()
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": float(request.get("socket_timeout", 12)),
            "retries": int(request.get("retries", 1)),
            "fragment_retries": int(request.get("fragment_retries", 1)),
            "extractor_retries": int(request.get("extractor_retries", 1)),
            "concurrent_fragment_downloads": int(request.get("concurrent_fragment_downloads", 4)),
            "format": str(request["format"]),
            "proxy": request.get("proxy") or "",
            "ffmpeg_location": request.get("ffmpeg_location") or None,
            "outtmpl": str(request["output_template"]),
            "cookiefile": request.get("cookie_file") or None,
            "http_headers": {"User-Agent": str(request["user_agent"])},
        }
        if request.get("js_runtimes"):
            options["js_runtimes"] = request["js_runtimes"]
        if request.get("merge_output_format"):
            options["merge_output_format"] = request["merge_output_format"]
        browser_cookie_source = request.get("browser_cookie_source")
        if browser_cookie_source:
            options["cookiesfrombrowser"] = tuple(browser_cookie_source)

        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(str(request["url"]), download=True)

        print(json.dumps({"ok": True, "info": _compact_info(info or {})}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)[:1200]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

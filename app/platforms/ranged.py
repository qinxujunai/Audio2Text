"""Download one large file over several HTTP Range connections.

CDNs such as Bilibili's throttle each connection, so a long video's picture stream (hundreds of
MB) arrives far sooner over a few parallel ranges. This is the technique of maintained
downloaders (Bili23-Downloader's multi-thread mode, aria2c's ``-x``), done with the httpx client
the app already uses instead of an extra binary.

``download_in_parts`` returns None when the server does not offer ranges or the file is small,
so the caller can fall back to one plain request.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import httpx

MIN_PART_BYTES = 8 * 1024 * 1024
ATTEMPTS_PER_PART = 3

ClientFactory = Callable[[], httpx.Client]


def probe_size(client_factory: ClientFactory, url: str) -> int | None:
    """Total size when the server answers a one-byte Range request with 206, else None."""
    with client_factory() as client:
        with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as response:
            if response.status_code != 206:
                return None
            match = re.search(r"/(\d+)\s*$", response.headers.get("content-range", ""))
            return int(match.group(1)) if match else None


def split(total: int, parts: int) -> list[tuple[int, int]]:
    """Inclusive byte ranges covering 0..total-1."""
    step = -(-total // parts)
    return [(start, min(start + step, total) - 1) for start in range(0, total, step)]


def _fetch_part(client_factory: ClientFactory, url: str, start: int, end: int, path: Path) -> None:
    expected = end - start + 1
    last_error: Exception | None = None
    for _ in range(ATTEMPTS_PER_PART):
        try:
            with client_factory() as client:
                with client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as response:
                    if response.status_code != 206:
                        raise httpx.HTTPError(f"range not honoured: HTTP {response.status_code}")
                    with open(path, "wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
            if path.stat().st_size == expected:
                return
            last_error = httpx.HTTPError(f"short part: {path.stat().st_size} of {expected} bytes")
        except httpx.HTTPError as exc:
            last_error = exc
    raise last_error or httpx.HTTPError("part failed")


def download_in_parts(
    client_factory: ClientFactory,
    url: str,
    target: Path,
    *,
    parts: int = 4,
    min_part_bytes: int = MIN_PART_BYTES,
) -> Path | None:
    total = probe_size(client_factory, url)
    if not total or total < 2 * min_part_bytes:
        return None
    ranges = split(total, max(1, min(parts, total // min_part_bytes)))
    part_paths = [target.with_name(f"{target.name}.part{index}") for index in range(len(ranges))]
    try:
        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            futures = [
                pool.submit(_fetch_part, client_factory, url, start, end, path)
                for (start, end), path in zip(ranges, part_paths)
            ]
            for future in futures:
                future.result()
        with open(target, "wb") as out:
            for path in part_paths:
                with open(path, "rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        out.write(chunk)
        if target.stat().st_size != total:
            target.unlink(missing_ok=True)
            raise httpx.HTTPError(f"assembled {target.stat().st_size if target.exists() else 0} of {total} bytes")
        return target
    finally:
        for path in part_paths:
            path.unlink(missing_ok=True)

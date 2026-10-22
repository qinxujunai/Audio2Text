"""Large files over several Range connections, with a plain fallback."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import httpx

from app.platforms import ranged

BODY = bytes(range(256)) * 400  # 102,400 bytes


def _server(*, ranges: bool = True, fail_first: set[int] | None = None):
    calls: list[str] = []
    failures = set(fail_first or ())
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        header = request.headers.get("range")
        with lock:
            calls.append(header or "")
        if not ranges or not header:
            return httpx.Response(200, content=BODY)
        start, end = (int(x) for x in header.removeprefix("bytes=").split("-"))
        with lock:
            if start in failures:
                failures.discard(start)
                return httpx.Response(500)
        return httpx.Response(
            206,
            content=BODY[start : end + 1],
            headers={"content-range": f"bytes {start}-{end}/{len(BODY)}"},
        )

    factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    return factory, calls


class RangedDownloadTestCase(unittest.TestCase):
    def test_split_covers_every_byte_once(self) -> None:
        self.assertEqual(ranged.split(10, 3), [(0, 3), (4, 7), (8, 9)])
        self.assertEqual(ranged.split(8, 4), [(0, 1), (2, 3), (4, 5), (6, 7)])

    def test_parts_are_fetched_in_parallel_and_assembled_in_order(self) -> None:
        factory, calls = _server()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "video.m4s"
            done = ranged.download_in_parts(factory, "https://cdn.example/v", target, parts=4, min_part_bytes=1024)
            self.assertEqual(done, target)
            self.assertEqual(target.read_bytes(), BODY)
            self.assertEqual(sorted(Path(tmp).iterdir()), [target], "part files are cleaned up")
        self.assertEqual(calls[0], "bytes=0-0")
        self.assertEqual(len(calls), 5)

    def test_a_failed_part_is_retried(self) -> None:
        factory, calls = _server(fail_first={25600})
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "video.m4s"
            ranged.download_in_parts(factory, "https://cdn.example/v", target, parts=4, min_part_bytes=1024)
            self.assertEqual(target.read_bytes(), BODY)
        self.assertEqual(calls.count("bytes=25600-51199"), 2)

    def test_no_range_support_or_small_file_means_fall_back(self) -> None:
        factory, _ = _server(ranges=False)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "video.m4s"
            self.assertIsNone(ranged.download_in_parts(factory, "https://cdn.example/v", target, min_part_bytes=1024))
            factory, _ = _server()
            self.assertIsNone(ranged.download_in_parts(factory, "https://cdn.example/v", target, min_part_bytes=len(BODY)))
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()

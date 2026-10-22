"""Douyin details come from the no-login share-page API first.

The fixture is a trimmed slidesinfo response recorded on 2026-10-22 (title and author replaced).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import app.extractors as extractors
from app.platforms import douyin

FIXTURE = Path(__file__).parent / "fixtures" / "douyin_slidesinfo_video.json"


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("empty body")
        return self._payload


class _Client:
    def __init__(self, answers: list, seen: list):
        self._answers = answers
        self._seen = seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        self._seen.append(url)
        return _Response(self._answers.pop(0) if self._answers else None)


class DouyinShareApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_urls_try_video_then_note(self) -> None:
        urls = douyin.slidesinfo_urls("123")
        self.assertEqual(urls[0], "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/?aweme_ids=%5B123%5D")
        self.assertTrue(urls[1].endswith("&request_source=200"))

    def test_item_comes_from_aweme_details(self) -> None:
        item = douyin.item_from_slidesinfo(self.payload)
        self.assertEqual(item["desc"], "示例视频描述")
        self.assertEqual(item["video"]["duration"], 116611)
        self.assertEqual(douyin.item_from_slidesinfo({"aweme_details": []}), {})
        self.assertEqual(douyin.item_from_slidesinfo(None), {})

    def test_fetch_asks_the_share_api_first_and_retries_an_empty_answer(self) -> None:
        seen: list[str] = []
        answers = [None, None, self.payload]  # video and note variants empty, then the retry answers
        with patch.object(extractors, "_platform_needs_relay", return_value=False), patch.object(
            extractors, "_httpx_client", side_effect=lambda **_: _Client(answers, seen)
        ):
            item = extractors._fetch_douyin_item("7624387162426887464")

        self.assertEqual(item["aweme_id"], "7624387162426887464")
        self.assertTrue(all("slidesinfo" in url for url in seen), seen)
        self.assertNotIn("iteminfo", " ".join(seen), "the retired endpoint must not come back")

    def test_media_urls_prefer_the_unwatermarked_stream(self) -> None:
        item = douyin.item_from_slidesinfo(self.payload)
        urls = extractors._collect_douyin_media_urls(item)
        self.assertEqual(urls[0], "https://v3-dy-o.zjcdn.com/example/video/tos/cn/play.mp4")
        self.assertIn("https://www.douyin.com/aweme/v1/play/?video_id=example", urls)
        self.assertFalse(any("/playwm/" in url for url in urls))


if __name__ == "__main__":
    unittest.main()

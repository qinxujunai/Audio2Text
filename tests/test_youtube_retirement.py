"""Retired inputs must be rejected before networking or a generic fallback."""
import unittest
import httpx
import tempfile
import app.pipeline as pipeline
import app.store_fs as store_fs
from pathlib import Path
from unittest.mock import patch

from app.resolver import ResolvedSource, resolve_url
from app.settings import URL_INPUT_PLATFORM_HINTS
from app.source_adapters import get_url_adapter
from app.extractors import ExtractionError
from scripts.validate_benchmark_reports import REQUIRED_PLATFORMS
from scripts import refresh_browser_session


class YouTubeRetirementTests(unittest.TestCase):
    def test_recovered_old_queue_stops_without_automatic_retry_or_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(store_fs, "CAPTURES_DIR", root / "captures"), patch.object(
                store_fs, "ARTIFACTS_DIR", root / "artifacts"
            ), patch.object(pipeline, "ARTIFACTS_DIR", root / "artifacts"), patch.object(
                pipeline, "enqueue_capture"
            ) as queue, patch.object(pipeline, "get_url_adapter") as provider:
                capture = pipeline.repository.create(input_type="url", source_platform="youtube",
                                                     content_type="video", url="https://youtu.be/public")
                pipeline.process_capture(capture.id)
                saved = pipeline.repository.get(capture.id)
                self.assertEqual(saved.processing.failure_reason_code, "source_unsupported")
                self.assertFalse(saved.processing.retryable)
                self.assertNotIn(saved.status, ("queued", "processing"))
                queue.assert_not_called()
                provider.assert_not_called()

    def test_session_refresh_never_opens_retired_source(self):
        self.assertEqual(refresh_browser_session.DEFAULT_URLS,
                         ["https://www.douyin.com/", "https://www.xiaohongshu.com/"])
        with patch("sys.argv", ["refresh_browser_session"]), patch.object(
            refresh_browser_session, "_target_url_from_file", return_value="https://youtu.be/public"
        ), patch.object(refresh_browser_session, "sync_playwright") as browser:
            with self.assertRaisesRegex(ValueError, "不再支持 YouTube"):
                refresh_browser_session.main()
            browser.assert_not_called()

    def test_links_are_rejected_without_dns_or_short_link_requests(self):
        urls = ["https://www.youtube.com/watch?v=public", "https://youtu.be/public",
                "https://M.YOUTUBE.COM.:443/shorts/public", "https://music.youtube.com/watch?v=public",
                "https://www.youtube-nocookie.com/embed/public"]
        with patch("app.resolver.assert_public_http_url") as dns, patch("app.resolver.httpx.Client") as network:
            for url in urls:
                with self.subTest(url=url), self.assertRaisesRegex(ValueError, "不再支持 YouTube"):
                    resolve_url(url)
            dns.assert_not_called()
            network.assert_not_called()

    def test_redirect_to_retired_source_is_rejected(self):
        with patch("app.resolver._expand_short_url", return_value="https://www.youtube.com/watch?v=public"), patch("app.resolver.assert_public_http_url"):
            with self.assertRaisesRegex(ValueError, "不再支持 YouTube"):
                resolve_url("https://b23.tv/public")

    def test_real_short_link_redirect_never_requests_retired_host(self):
        requests = []
        client_class = httpx.Client
        def transport(request):
            requests.append(request.url.host)
            if request.url.host == "b23.tv":
                return httpx.Response(302, headers={"Location": "https://www.youtube.com/watch?v=public"})
            return httpx.Response(200, text="retired page")
        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(transport), **kwargs)
        with patch("app.resolver.httpx.Client", side_effect=client), patch("app.resolver.assert_public_http_url"):
            with self.assertRaisesRegex(ValueError, "不再支持 YouTube"):
                resolve_url("https://b23.tv/public")
        self.assertEqual(requests, ["b23.tv"])

    def test_adapter_cannot_revive_a_retired_source(self):
        source = ResolvedSource("", "", "https://www.youtube.com/watch?v=public", "youtube", "video", "YouTube")
        with self.assertRaises(ExtractionError) as raised:
            get_url_adapter(source)
        self.assertEqual(raised.exception.reason_code, "source_unsupported")
        self.assertFalse(raised.exception.retryable)

    def test_supported_platforms_and_public_copy_match_new_scope(self):
        expected = {"bilibili", "xiaoyuzhou", "douyin", "xiaohongshu", "wechat_article"}
        self.assertEqual(REQUIRED_PLATFORMS, expected)
        self.assertNotIn("youtube", URL_INPUT_PLATFORM_HINTS)
        for name in ("site/index.html", "site/app.js", "README.md"):
            with self.subTest(name=name):
                self.assertNotIn("YouTube", Path(name).read_text(encoding="utf-8"))

    def test_remaining_platforms_keep_their_adapters(self):
        with patch("app.resolver.assert_public_http_url"):
            for url, platform in (("https://www.bilibili.com/video/BVpublic", "bilibili"),
                                  ("https://www.xiaoyuzhoufm.com/episode/public", "xiaoyuzhou"),
                                  ("https://www.douyin.com/video/12345678901", "douyin"),
                                  ("https://www.xiaohongshu.com/explore/public", "xiaohongshu"),
                                  ("https://mp.weixin.qq.com/s/public", "wechat_article")):
                resolved = resolve_url(url)
                self.assertEqual(resolved.platform, platform)
                self.assertEqual(get_url_adapter(resolved).platform, platform)

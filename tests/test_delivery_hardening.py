from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app.browser_provider as browser_provider
import app.extractors as extractors
import app.pipeline as pipeline
import app.resolver as resolver
import app.source_adapters as source_adapters
import scripts.release_gate as release_gate


class _FakeResponse:
    def __init__(self, *, url: str = "https://example.com/final", text: str = "<html></html>") -> None:
        self.url = url
        self.text = text
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}

    def iter_bytes(self):
        yield b"ok"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeHttpxClient:
    last_kwargs: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        _FakeHttpxClient.last_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def get(self, url: str):
        return _FakeResponse(url="https://example.com/final", text="<title>sample</title>")

    def stream(self, method: str, url: str):
        return _FakeResponse(url=url)


class _RetryOnceResponse:
    attempts = 0

    def __init__(self, url: str) -> None:
        self.url = url

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        type(self).attempts += 1
        if type(self).attempts == 1:
            raise extractors.httpx.RemoteProtocolError("incomplete body")
        yield b"ok"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _RetryOnceClient:
    def __init__(self, *args, **kwargs) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def stream(self, method: str, url: str):
        return _RetryOnceResponse(url)


class DeliveryHardeningTestCase(unittest.TestCase):
    def test_xiaohongshu_browser_fallback_reaches_navigation_with_bounded_timeout(self) -> None:
        page = Mock()
        page.goto.side_effect = browser_provider.BrowserProviderError("导航超时", reason_code="browser_timeout")
        with patch.object(browser_provider, "_fetch_xiaohongshu_html", return_value=("https://www.xiaohongshu.com/explore/demo", "", "<html></html>")), patch.object(browser_provider, "_fetch_xiaohongshu_relay_fallback", return_value=("https://www.xiaohongshu.com/explore/demo", "", "<html></html>")), patch.object(browser_provider, "_browser_needs_relay", return_value=False), patch.object(browser_provider, "_launch_context") as launch:
            launch.return_value.__enter__.return_value = SimpleNamespace(pages=[page])
            with self.assertRaises(browser_provider.BrowserProviderError) as raised:
                browser_provider._fetch_xiaohongshu_page_once("https://www.xiaohongshu.com/explore/demo")
        self.assertEqual(raised.exception.reason_code, "browser_timeout")
        page.goto.assert_called_once_with("https://www.xiaohongshu.com/explore/demo", wait_until="domcontentloaded", timeout=browser_provider.BROWSER_GOTO_TIMEOUT_MS)

    def test_douyin_browser_navigation_honors_the_adapter_timeout(self) -> None:
        page = Mock()
        page.goto.side_effect = browser_provider.BrowserProviderError("导航超时", reason_code="browser_timeout")
        with patch.object(browser_provider, "_browser_needs_relay", return_value=False), patch.object(browser_provider, "_launch_context") as launch:
            launch.return_value.__enter__.return_value = SimpleNamespace(pages=[page])
            with self.assertRaises(browser_provider.BrowserProviderError) as raised:
                browser_provider.fetch_douyin_media("https://www.douyin.com/video/demo", goto_timeout_ms=25000)
        self.assertEqual(raised.exception.reason_code, "browser_timeout")
        page.goto.assert_called_once_with("https://www.douyin.com/video/demo", wait_until="domcontentloaded", timeout=25000)

    def test_frontend_mount_has_a_non_blank_render_recovery_surface(self) -> None:
        source = (Path(__file__).parents[1] / "web" / "src" / "main.tsx").read_text(encoding="utf-8")

        self.assertIn("AppErrorBoundary", source)
        self.assertIn("页面需要恢复", source)
        self.assertIn("重新加载", source)

    def test_public_ui_contract_accepts_current_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist = Path(tmp_dir)
            (dist / "assets").mkdir()
            (dist / "index.html").write_text("<script src='/assets/app.js'></script>", encoding="utf-8")
            (dist / "assets" / "app.js").write_text("const indicator = true;", encoding="utf-8")

            with patch.object(release_gate, "FRONTEND_DIST_DIR", dist):
                release_gate._assert_public_ui_contract()

    def test_public_ui_contract_rejects_retired_elements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist = Path(tmp_dir)
            (dist / "index.html").write_text("<div class='processing-mascot'>卡吱</div>", encoding="utf-8")

            with patch.object(release_gate, "FRONTEND_DIST_DIR", dist):
                with self.assertRaisesRegex(RuntimeError, "processing-mascot"):
                    release_gate._assert_public_ui_contract()

    def test_xiaohongshu_initial_state_parser_accepts_html_spacing_and_semicolon(self) -> None:
        html = "<script>window.__INITIAL_STATE__ = {\"note\": {\"noteDetailMap\": {}}};</script>"
        state = browser_provider._extract_xiaohongshu_state(html)
        self.assertEqual(state, {"note": {"noteDetailMap": {}}})

    def test_xiaohongshu_short_link_recovery_accepts_escaped_and_relative_note_urls(self) -> None:
        escaped = r'<script>const target = "https:\/\/www.xiaohongshu.com\/explore\/abc123?xsec_token=demo";</script>'
        relative = '<a href="/discovery/item/def456?xsec_token=demo">打开笔记</a>'
        mobile = '<meta property="og:url" content="https://m.xiaohongshu.com/note/ghi789?xsec_token=demo">'

        self.assertEqual(
            browser_provider._extract_xiaohongshu_full_url_from_html(
                escaped,
                base_url="https://www.xiaohongshu.com/",
            ),
            "https://www.xiaohongshu.com/explore/abc123?xsec_token=demo",
        )
        self.assertEqual(
            browser_provider._extract_xiaohongshu_full_url_from_html(
                relative,
                base_url="https://xhslink.com/a/demo",
            ),
            "https://www.xiaohongshu.com/discovery/item/def456?xsec_token=demo",
        )
        self.assertEqual(
            browser_provider._extract_xiaohongshu_full_url_from_html(mobile),
            "https://m.xiaohongshu.com/note/ghi789?xsec_token=demo",
        )

    def test_xiaohongshu_login_landing_does_not_start_a_second_browser_attempt(self) -> None:
        with patch.object(
            browser_provider,
            "_fetch_xiaohongshu_html",
            return_value=(
                "https://www.xiaohongshu.com/login?redirectPath=%2Fexplore%3FexSource%3Dnull",
                "登录",
                "<html><title>登录</title></html>",
            ),
        ), patch.object(browser_provider, "_launch_context") as launch_context:
            self.assertEqual(
                browser_provider.resolve_xiaohongshu_share_url("https://xhslink.com/a/demo"),
                "",
            )

        launch_context.assert_not_called()

    def test_browser_runtime_refresh_drops_context_without_deleting_profile(self) -> None:
        class FakeContext:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        runtime = browser_provider.BrowserRuntime(max_tasks=2, idle_seconds=3600)
        context = FakeContext()
        runtime._context = context
        runtime._last_error_code = "browser_challenge_required"

        runtime.refresh()

        self.assertTrue(context.closed)
        self.assertEqual(runtime.status()["state"], "idle")
        self.assertEqual(runtime.status()["last_error_code"], "manual_refresh")

    def test_xiaohongshu_retries_once_after_browser_challenge(self) -> None:
        expected = browser_provider.BrowserMediaResult(
            final_url="https://www.xiaohongshu.com/explore/demo",
            title="demo",
            media_url="",
            body_text="正文",
            image_urls=["https://example.com/image.jpg"],
            live_photo_video_urls=[""],
        )
        challenge = browser_provider.BrowserProviderError(
            "需要刷新浏览器会话。",
            reason_code="browser_challenge_required",
        )

        with patch.object(
            browser_provider,
            "_fetch_xiaohongshu_page_once",
            side_effect=[challenge, expected],
        ) as fetch_once, patch.object(browser_provider, "refresh_browser_runtime") as refresh:
            result = browser_provider.fetch_xiaohongshu_page("https://www.xiaohongshu.com/explore/demo")

        self.assertIs(result, expected)
        self.assertEqual(fetch_once.call_count, 2)
        refresh.assert_called_once_with()

    def test_xiaohongshu_uses_relay_after_local_public_path_misses(self) -> None:
        state_html = (
            '<script>window.__INITIAL_STATE__ = '
            '{"note":{"noteDetailMap":{"demo":{"note":{'
            '"title":"relay note","desc":"relay body",'
            '"imageList":[{"urlDefault":"https://example.com/image.jpg"}]'
            '}}}}};</script>'
        )

        with patch.object(
            browser_provider,
            "_fetch_xiaohongshu_html",
            return_value=("https://www.xiaohongshu.com/explore/demo", "", "<html></html>"),
        ), patch.object(
            browser_provider,
            "_fetch_xiaohongshu_relay_fallback",
            return_value=("https://www.xiaohongshu.com/explore/demo", "", state_html),
        ) as relay, patch.object(browser_provider, "_browser_needs_relay", return_value=False), patch.object(
            browser_provider,
            "_launch_context",
        ) as launch_context:
            result = browser_provider._fetch_xiaohongshu_page_once(
                "https://www.xiaohongshu.com/explore/demo"
            )

        self.assertEqual(result.title, "relay note")
        self.assertEqual(result.image_urls, ["https://example.com/image.jpg"])
        relay.assert_called_once_with("https://www.xiaohongshu.com/explore/demo")
        launch_context.assert_not_called()

    def test_browser_runtime_reuses_context_and_recycles_at_task_limit(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class FakeContext:
            def __init__(self) -> None:
                self.pages: list[FakePage] = []
                self.closed = False

            def new_page(self) -> FakePage:
                page = FakePage()
                self.pages.append(page)
                return page

            def close(self) -> None:
                self.closed = True

        runtime = browser_provider.BrowserRuntime(max_tasks=2, idle_seconds=3600)
        contexts: list[FakeContext] = []

        def fake_start() -> None:
            runtime._stop_locked()
            context = FakeContext()
            contexts.append(context)
            runtime._context = context
            runtime._owner_thread_id = browser_provider.threading.get_ident()
            runtime._started_monotonic = browser_provider.time.monotonic()
            runtime._last_used_monotonic = runtime._started_monotonic
            runtime._task_count = 0
            runtime._needs_restart = False

        with patch.object(runtime, "_start_locked", side_effect=fake_start):
            with runtime.lease() as first:
                self.assertIs(first, contexts[0])
            with runtime.lease() as second:
                self.assertIs(second, contexts[0])
            with runtime.lease() as third:
                self.assertIs(third, contexts[1])

        self.assertEqual(len(contexts), 2)
        self.assertTrue(contexts[0].closed)
        status = runtime.status()
        self.assertEqual(status["tasks_since_restart"], 1)
        self.assertEqual(status["restart_count"], 1)
        self.assertNotIn("profile", str(status).lower())

    def test_short_url_expansion_uses_default_tls_verification(self) -> None:
        with patch.object(resolver.httpx, "Client", _FakeHttpxClient):
            expanded = resolver._expand_short_url("https://b23.tv/demo")

        self.assertEqual(expanded, "https://example.com/final")
        self.assertNotIn("verify", _FakeHttpxClient.last_kwargs)

    def test_extractors_fetch_text_uses_default_tls_verification(self) -> None:
        with patch.object(extractors.httpx, "Client", _FakeHttpxClient):
            final_url, html = extractors._fetch_text("https://example.com/post")

        self.assertEqual(final_url, "https://example.com/final")
        self.assertIn("<title>sample</title>", html)
        self.assertNotIn("verify", _FakeHttpxClient.last_kwargs)

    def test_pipeline_image_download_uses_default_tls_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "image.jpg"
            with patch.object(pipeline.httpx, "Client", _FakeHttpxClient):
                saved = pipeline._download_image("https://example.com/image.jpg", target)

            self.assertEqual(saved.name, "image.jpg")
            self.assertNotIn("verify", _FakeHttpxClient.last_kwargs)
            self.assertTrue(target.exists())

    def test_browser_provider_fetch_uses_default_tls_verification(self) -> None:
        with patch.object(browser_provider.httpx, "Client", _FakeHttpxClient):
            final_url, title, html = browser_provider._fetch_xiaohongshu_html("https://example.com/note")

        self.assertEqual(final_url, "https://example.com/final")
        self.assertEqual(title, "sample")
        self.assertIn("<title>sample</title>", html)
        self.assertNotIn("verify", _FakeHttpxClient.last_kwargs)

    def test_source_adapter_browser_download_uses_default_tls_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "media.mp4"
            with patch.object(source_adapters.httpx, "Client", _FakeHttpxClient):
                saved = source_adapters._download_browser_media(
                    "https://example.com/media.mp4",
                    target,
                    "https://example.com/referer",
                )

            self.assertEqual(saved.name, "media.mp4")
            self.assertNotIn("verify", _FakeHttpxClient.last_kwargs)
            self.assertTrue(target.exists())

    def test_extractors_download_retries_after_incomplete_stream(self) -> None:
        _RetryOnceResponse.attempts = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "media.mp4"
            with patch.object(extractors.httpx, "Client", _RetryOnceClient):
                saved = extractors._download_file("https://example.com/media.mp4", target)

            self.assertEqual(saved.name, "media.mp4")
            self.assertTrue(target.exists())
            self.assertEqual(_RetryOnceResponse.attempts, 2)

    def test_source_adapter_download_retries_after_incomplete_stream(self) -> None:
        _RetryOnceResponse.attempts = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "media.mp4"
            with patch.object(source_adapters.httpx, "Client", _RetryOnceClient):
                saved = source_adapters._download_browser_media(
                    "https://example.com/media.mp4",
                    target,
                    "https://example.com/referer",
                )

            self.assertEqual(saved.name, "media.mp4")
            self.assertTrue(target.exists())
            self.assertEqual(_RetryOnceResponse.attempts, 2)

    def test_douyin_adapter_excludes_managed_bridge_provider(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://www.douyin.com/video/1234567890")
        providers = source_adapters.DouyinAdapter().build_providers(resolved, Path(tempfile.gettempdir()))

        self.assertEqual(
            [provider.name for provider in providers],
            ["direct_provider", "browser_provider", "open_source_provider"],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.browser_provider as browser_provider
import app.extractors as extractors
import app.pipeline as pipeline
import app.resolver as resolver
import app.source_adapters as source_adapters


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

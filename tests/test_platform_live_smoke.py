from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

import httpx

from tests.live_smoke_helpers import load_live_smoke_samples


def _load_config() -> tuple[str | None, Path | None]:
    base_url = os.environ.get("AUDIO2TEXT_LIVE_SMOKE_BASE_URL", "").strip() or None
    sample_path = os.environ.get("AUDIO2TEXT_LIVE_SMOKE_SAMPLES", "").strip() or "tests/live_smoke_samples.json"
    path = Path(sample_path)
    return base_url, (path if path.exists() else None)


class PlatformLiveSmokeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_url, cls.sample_path = _load_config()
        if not cls.base_url:
            raise unittest.SkipTest("AUDIO2TEXT_LIVE_SMOKE_BASE_URL is not configured.")
        if cls.sample_path is None:
            raise unittest.SkipTest("Live smoke sample file is missing.")

        cls.samples = load_live_smoke_samples(cls.sample_path)
        cls.client = httpx.Client(base_url=cls.base_url, timeout=60.0)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "client"):
            cls.client.close()

    def _run_capture(self, url: str) -> dict:
        created = self.client.post("/v1/captures", json={"text": url})
        created.raise_for_status()
        capture_id = created.json()["capture_id"]

        deadline = time.time() + 360
        while time.time() < deadline:
            response = self.client.get(f"/v1/captures/{capture_id}")
            response.raise_for_status()
            payload = response.json()
            if payload["capture"]["status"] in {"done", "failed"}:
                return payload
            time.sleep(2)
        self.fail(f"capture timed out: {url}")

    def test_success_samples(self) -> None:
        for sample in self.samples.get("success", []):
            with self.subTest(platform=sample["platform"], url=sample["url"]):
                payload = self._run_capture(sample["url"])
                self.assertEqual(payload["capture"]["status"], "done")
                self.assertTrue(payload["result"]["primary_text"] or payload["source"]["image_urls"])

                expected_media_kind = sample.get("expected_media_kind", "").strip()
                if expected_media_kind:
                    self.assertEqual(payload["source"]["media_kind"], expected_media_kind)

                expected_text_source = sample.get("expected_text_source", "").strip()
                if expected_text_source:
                    self.assertEqual(payload["quality"]["text_source"], expected_text_source)

                if sample.get("require_text", payload["source"]["media_kind"] == "video"):
                    self.assertTrue(payload["result"]["primary_text"])

                if sample.get("require_source_media", payload["source"]["media_kind"] == "video"):
                    self.assertTrue(
                        any(item["type"] == "source_media" for item in payload["artifacts"]),
                        f"expected source_media artifact for {sample['platform']}",
                    )

                if sample.get("require_source_audio", False):
                    self.assertTrue(
                        any(item["type"] == "source_audio" for item in payload["artifacts"]),
                        f"expected source_audio artifact for {sample['platform']}",
                    )

                if sample.get("require_image_urls", False):
                    self.assertTrue(payload["source"]["image_urls"])

                if sample.get("require_live_artifact", False):
                    self.assertTrue(
                        any(item["type"].startswith("image_live_") for item in payload["artifacts"]),
                        f"expected image_live artifact for {sample['platform']}",
                    )

    def test_failure_samples(self) -> None:
        for sample in self.samples.get("failure", []):
            with self.subTest(platform=sample["platform"], url=sample["url"]):
                payload = self._run_capture(sample["url"])
                self.assertEqual(payload["capture"]["status"], "failed")
                self.assertTrue(payload["capture"]["error_stage"])
                self.assertTrue(payload["capture"]["error_message"])
                expected_error_stage = sample.get("expected_error_stage", "").strip()
                if expected_error_stage:
                    self.assertEqual(payload["capture"]["error_stage"], expected_error_stage)


if __name__ == "__main__":
    unittest.main()

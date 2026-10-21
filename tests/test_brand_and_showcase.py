from __future__ import annotations

import json
import hashlib
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BrandAndShowcaseTestCase(unittest.TestCase):
    def test_mobile_home_is_the_reviewed_retirement_capture(self) -> None:
        # This public screenshot was visually reviewed with an empty history.
        # Text-only checks cannot detect retired-platform copy inside a PNG.
        self.assertEqual(
            hashlib.sha256((ROOT / 'docs/assets/home-mobile.png').read_bytes()).hexdigest(),
            'd17f6d027f030da5a715294a8ea314c2b8646dca4d777996f8104851ecc9eab0',
        )

    def test_public_promotion_excludes_retired_platform(self) -> None:
        for relative in ("site/index.html", "site/app.js", "README.md"):
            self.assertNotIn("youtube", (ROOT / relative).read_text(encoding="utf-8").lower(), relative)

    def test_candidate_workflow_cannot_implicitly_publish_pages(self) -> None:
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        self.assertNotIn("workflow_run", workflow)

    def test_product_app_uses_the_compact_praxis_mark(self) -> None:
        source = (ROOT / "web/public/brand-mark.svg").read_bytes()
        self.assertEqual(source, (ROOT / "design/brand/app-icon.svg").read_bytes())
        text = source.decode("utf-8")
        self.assertNotIn("<text", text)
        self.assertIn('data-symbol="praxis-dot"', text)
        self.assertIn("#111113", text)
        self.assertIn("#FFFFFF", text)
        self.assertNotIn("#0A84FF", text)
        self.assertNotIn("#30B38C", text)
        self.assertNotIn("#F2B84B", text)

    def test_every_product_surface_uses_the_original_round_mark(self) -> None:
        source = (ROOT / "web/public/brand-mark.svg").read_bytes()
        for relative in ("site/assets/brand-mark.svg", "site/assets/brand-mark-inverse.svg",
                         "showcase/public/brand-mark.svg"):
            self.assertEqual(source, (ROOT / relative).read_bytes(), relative)

    def test_required_public_assets_exist(self) -> None:
        for relative in (
            "site/assets/favicon.png",
            "site/assets/demo-poster.jpg",
            "site/assets/social-preview.png",
            "site/assets/wanxiang-demo.mp4",
            "docs/assets/wanxiang-icon-1024.png",
            "web/src-tauri/icons/icon.ico",
        ):
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertGreater(path.stat().st_size, 0, relative)

    def test_ui_video_fixture_is_a_real_small_mp4(self) -> None:
        path = ROOT / "tests/fixtures/ui-preview.mp4"
        self.assertTrue(path.is_file())
        self.assertLess(path.stat().st_size, 100_000)
        data = path.read_bytes()
        self.assertIn(b"ftyp", data[:64])
        self.assertIn(b"avc1", data)

    def test_demo_video_contract(self) -> None:
        process = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration,size:stream=codec_name,codec_type,width,height,r_frame_rate,pix_fmt",
                "-of", "json", str(ROOT / "docs/assets/wanxiang-demo.mp4"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(process.stdout)
        streams = payload["streams"]
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["codec_type"], "video")
        self.assertEqual(streams[0]["codec_name"], "h264")
        self.assertEqual((streams[0]["width"], streams[0]["height"]), (1920, 1080))
        self.assertEqual(streams[0]["r_frame_rate"], "30/1")
        self.assertEqual(streams[0]["pix_fmt"], "yuv420p")
        self.assertAlmostEqual(float(payload["format"]["duration"]), 12.0, places=2)
        self.assertLess(int(payload["format"]["size"]), 3 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()

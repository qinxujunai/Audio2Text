from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BrandAndShowcaseTestCase(unittest.TestCase):
    def test_brand_assets_share_the_same_svg(self) -> None:
        source = (ROOT / "web/public/brand-mark.svg").read_bytes()
        self.assertEqual(source, (ROOT / "showcase/public/brand-mark.svg").read_bytes())
        self.assertEqual(source, (ROOT / "site/assets/brand-mark.svg").read_bytes())
        text = source.decode("utf-8")
        self.assertNotIn("<text", text)
        self.assertIn("#0A84FF", text)
        self.assertIn("#30B38C", text)
        self.assertIn("#F2B84B", text)

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

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

from scripts.prepare_public_site import _render_site


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = ((360, 780), (390, 844), (1440, 900), (2560, 1440))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class PublicSiteBrowserTests(unittest.TestCase):
    browser: Browser
    server: subprocess.Popen[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_root = Path(tempfile.mkdtemp(prefix="wanxiang-site-browser-"))
        cls.site_dir = cls.temp_root / "site"
        metadata = {
            "version": "2.1.0",
            "installer_url": "https://github.com/qinxujunai/Audio2Text/releases/download/v2.1.0/Wanxiang-Windows-x64-Setup.exe",
            "installer_size_label": "146.0 MB",
            "sha256": "a" * 64,
        }
        _render_site(PROJECT_ROOT / "site", cls.site_dir, metadata, trial_available=True)
        cls.port = _free_port()
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(cls.port), "--bind", "127.0.0.1", "--directory", str(cls.site_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("public site test server did not start")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()
        cls.server.terminate()
        cls.server.wait(timeout=5)
        shutil.rmtree(cls.temp_root, ignore_errors=True)

    def _open(self, width: int, height: int, *, reduced_motion: bool = False) -> Page:
        page = self.browser.new_page(viewport={"width": width, "height": height}, locale="zh-CN")
        if reduced_motion:
            page.emulate_media(reduced_motion="reduce")
        page.goto(f"http://127.0.0.1:{self.port}", wait_until="networkidle")
        return page

    def test_supported_viewports_have_no_horizontal_overflow(self) -> None:
        for width, height in VIEWPORTS:
            with self.subTest(viewport=f"{width}x{height}"):
                page = self._open(width, height)
                self.assertEqual(page.locator("h1").inner_text(), "万象成文")
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)
                page.close()

    def test_mobile_interactive_targets_are_at_least_44_pixels(self) -> None:
        page = self._open(360, 780)
        boxes = page.locator("a[href], button").evaluate_all(
            "els => els.map(el => ({text: el.textContent.trim(), rect: el.getBoundingClientRect().toJSON()})).filter(item => item.rect.width > 0 && item.rect.height > 0)"
        )
        undersized = [item for item in boxes if item["rect"]["width"] < 44 or item["rect"]["height"] < 44]
        self.assertEqual(undersized, [])
        page.close()

    def test_language_choice_persists(self) -> None:
        page = self._open(390, 844)
        page.get_by_role("button", name="EN").click()
        self.assertEqual(page.locator("html").get_attribute("lang"), "en")
        self.assertIn("Turn videos", page.locator(".hero-lede").inner_text())
        page.reload(wait_until="networkidle")
        self.assertEqual(page.locator("html").get_attribute("lang"), "en")
        page.close()

    def test_reduced_motion_does_not_autoplay_video(self) -> None:
        page = self._open(390, 844, reduced_motion=True)
        self.assertTrue(page.locator("video").evaluate("video => video.paused"))
        self.assertTrue(page.locator("video").get_attribute("poster").endswith("demo-poster.jpg"))
        page.close()

    def test_rendered_release_metadata_matches_page(self) -> None:
        metadata = json.loads((self.site_dir / "release-metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["version"], "2.1.0")
        self.assertTrue(metadata["trial_available"])
        page = self._open(1440, 900)
        self.assertEqual(page.locator(".trial-link").count(), 2)
        self.assertIn("v2.1.0", page.locator(".compatibility").inner_text())
        page.close()


if __name__ == "__main__":
    unittest.main()

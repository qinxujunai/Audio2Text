from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import prepare_public_site


class PublicSiteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.checksums = Path(self.tempdir.name) / "SHA256SUMS.txt"
        self.checksums.write_text(f"{'a' * 64}  Wanxiang-Windows-x64-Setup.exe\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _release(self, *, version: str = "2.1.0") -> dict:
        return {
            "tag_name": f"v{version}",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "Wanxiang-Windows-x64-Setup.exe",
                    "state": "uploaded",
                    "size": 104857600,
                    "digest": "sha256:" + "a" * 64,
                    "browser_download_url": "https://example.com/Wanxiang-Windows-x64-Setup.exe",
                },
                {
                    "name": "SHA256SUMS.txt",
                    "state": "uploaded",
                    "size": 100,
                    "digest": "sha256:" + "b" * 64,
                    "browser_download_url": "https://example.com/SHA256SUMS.txt",
                },
            ],
        }

    def test_release_metadata_is_validated(self) -> None:
        metadata = prepare_public_site._validated_release(self._release(), checksums_file=str(self.checksums))
        self.assertEqual(metadata["version"], "2.1.0")
        self.assertEqual(metadata["installer_size_label"], "100.0 MB")
        self.assertEqual(metadata["sha256"], "a" * 64)

    def test_invalid_release_is_rejected(self) -> None:
        release = self._release()
        release["assets"][0]["digest"] = None
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            prepare_public_site._validated_release(release, checksums_file=str(self.checksums))

    def test_checksum_mismatch_is_rejected(self) -> None:
        self.checksums.write_text(f"{'c' * 64}  Wanxiang-Windows-x64-Setup.exe\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            prepare_public_site._validated_release(self._release(), checksums_file=str(self.checksums))

    def test_trial_requires_matching_cloud_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            health = root / "health.json"
            config = root / "config.json"
            health.write_text(json.dumps({"status": "ok", "version": "2.1.0", "deployment_mode": "cloud_preview", "runtime_target": "cloud_demo"}), encoding="utf-8")
            config.write_text(json.dumps({"deployment_mode": "cloud_preview", "runtime_target": "cloud_demo"}), encoding="utf-8")
            self.assertTrue(prepare_public_site._trial_is_current("2.1.0", health_path=str(health), config_path=str(config)))
            self.assertFalse(prepare_public_site._trial_is_current("2.0.2", health_path=str(health), config_path=str(config)))

    def test_render_hides_stale_trial_and_replaces_release_markers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "rendered"
            metadata = prepare_public_site._validated_release(self._release(), checksums_file=str(self.checksums))
            prepare_public_site._render_site(prepare_public_site.PROJECT_ROOT / "site", output, metadata, trial_available=False)
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("__WANXIANG_", html)
            self.assertNotIn("trial-link", html)
            self.assertIn("v2.1.0", html)
            self.assertIn("a" * 64, html)


if __name__ == "__main__":
    unittest.main()

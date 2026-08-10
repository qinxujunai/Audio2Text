from __future__ import annotations

import unittest
from pathlib import Path

from tests.live_smoke_helpers import find_committed_sample_url_hygiene_issues, load_live_smoke_samples


class LiveSmokeSamplesContractTestCase(unittest.TestCase):
    def test_default_live_smoke_samples_cover_required_platforms(self) -> None:
        sample_path = Path("tests/live_smoke_samples.json")
        payload = load_live_smoke_samples(sample_path)

        self.assertIsInstance(payload.get("success"), list)
        self.assertIsInstance(payload.get("failure"), list)
        self.assertGreaterEqual(len(payload["success"]), 6)

    def test_default_live_smoke_samples_do_not_commit_share_tokens(self) -> None:
        sample_path = Path("tests/live_smoke_samples.json")
        payload = load_live_smoke_samples(sample_path)

        self.assertEqual(find_committed_sample_url_hygiene_issues(payload), [])

    def test_rejects_reused_xiaohongshu_variant_urls(self) -> None:
        sample_path = Path("tests/live_smoke_samples.json")
        payload = load_live_smoke_samples(sample_path)
        xiaohongshu = [
            item for item in payload["success"] if item["platform"] == "xiaohongshu"
        ]
        xiaohongshu[1]["url"] = xiaohongshu[0]["url"]

        from tests.live_smoke_helpers import validate_live_smoke_samples

        self.assertIn(
            "xiaohongshu success variants must use distinct sample URLs",
            validate_live_smoke_samples(payload),
        )


if __name__ == "__main__":
    unittest.main()

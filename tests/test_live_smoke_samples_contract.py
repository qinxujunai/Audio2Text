from __future__ import annotations

import unittest
from pathlib import Path

from tests.live_smoke_helpers import load_live_smoke_samples


class LiveSmokeSamplesContractTestCase(unittest.TestCase):
    def test_default_live_smoke_samples_cover_required_platforms(self) -> None:
        sample_path = Path("tests/live_smoke_samples.json")
        payload = load_live_smoke_samples(sample_path)

        self.assertIsInstance(payload.get("success"), list)
        self.assertIsInstance(payload.get("failure"), list)
        self.assertGreaterEqual(len(payload["success"]), 6)


if __name__ == "__main__":
    unittest.main()

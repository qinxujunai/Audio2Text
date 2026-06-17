from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = (
    ROOT / "README.md",
    *(ROOT / "docs").glob("*.md"),
)
STALE_PATH_MARKERS = (
    "/e:/Files/Projects/" + "Audio2Text",
    "E:\\Files\\Projects\\" + "Audio2Text",
)


class DocsContractTestCase(unittest.TestCase):
    def test_docs_do_not_link_to_stale_local_paths(self) -> None:
        offenders: list[str] = []
        for path in DOC_PATHS:
            text = path.read_text(encoding="utf-8")
            for marker in STALE_PATH_MARKERS:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains {marker}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

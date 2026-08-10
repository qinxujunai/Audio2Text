from __future__ import annotations

import re
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopContractTests(unittest.TestCase):
    def test_frontend_invokes_registered_tauri_commands(self) -> None:
        frontend = (ROOT / "web" / "src" / "api.ts").read_text(encoding="utf-8")
        rust = (ROOT / "web" / "src-tauri" / "src" / "lib.rs").read_text(
            encoding="utf-8"
        )

        invoked = set(re.findall(r'invoke(?:<[^>]+>)?\("([a-z_]+)"', frontend))
        handler_match = re.search(
            r"generate_handler!\[(.*?)\]", rust, flags=re.DOTALL
        )
        self.assertIsNotNone(handler_match)
        registered = set(re.findall(r"\b([a-z][a-z0-9_]+)\b", handler_match.group(1)))

        self.assertTrue(invoked)
        self.assertEqual(set(), invoked - registered)

    def test_runtime_manifest_has_a_stable_bundle_path(self) -> None:
        config = json.loads(
            (ROOT / "web" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        resources = config["bundle"]["resources"]
        self.assertEqual(resources["../../runtime-packs.json"], "runtime-packs.json")
        self.assertEqual(resources["sidecar/"], "sidecar/")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class WorkerSecurityTestCase(unittest.TestCase):
    def test_cloudflare_worker_security_contract(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            ["node", "--test", "scripts/cf_worker_security.test.mjs"],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, (completed.stdout or "") + (completed.stderr or ""))


if __name__ == "__main__":
    unittest.main()

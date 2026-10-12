from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.build_fingerprint import source_fingerprint


class BuildFingerprintTests(unittest.TestCase):
    def test_snapshot_detects_untracked_changes_and_excludes_ignored_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("workspace/\n", encoding="utf-8")
            (root / "module.py").write_text("one", encoding="utf-8")
            baseline = source_fingerprint(root)
            (root / "workspace").mkdir()
            (root / "workspace/private.txt").write_text("private", encoding="utf-8")
            self.assertEqual(baseline, source_fingerprint(root))
            (root / "module.py").write_text("two", encoding="utf-8")
            self.assertNotEqual(baseline["source_fingerprint"], source_fingerprint(root)["source_fingerprint"])
            (root / "module.py").unlink()
            self.assertNotIn("module.py", source_fingerprint(root)["files"])

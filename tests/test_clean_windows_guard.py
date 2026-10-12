from pathlib import Path
import tempfile
import unittest

from scripts.verify_clean_windows import assert_clean_runner


class CleanWindowsGuardTests(unittest.TestCase):
    def test_requires_fresh_ci_profile_before_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows", "RUNNER_TEMP": str(root)}
            self.assertEqual(assert_clean_runner(env, root/'absent'), root.resolve())
            for key in ('GITHUB_ACTIONS', 'RUNNER_OS', 'RUNNER_TEMP'):
                invalid = dict(env); invalid.pop(key)
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    assert_clean_runner(invalid, root/'absent')
            with self.assertRaises(RuntimeError):
                assert_clean_runner(env, root)

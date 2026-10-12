from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from scripts.verify_clean_windows import assert_clean_runner, ci_debug_policy


class CleanWindowsGuardTests(unittest.TestCase):
    def test_elevated_override_is_ci_only_and_restores_after_failure(self):
        registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, KEY_SET_VALUE=2, KEY_QUERY_VALUE=4, KEY_WOW64_64KEY=8, REG_SZ=1, CreateKeyEx=MagicMock(), QueryValueEx=MagicMock(side_effect=FileNotFoundError), SetValueEx=MagicMock(), DeleteValue=MagicMock())
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'winreg':registry}):
            root = Path(directory)
            with self.assertRaises(RuntimeError):
                with ci_debug_policy({},root/'absent'): pass
            registry.CreateKeyEx.assert_not_called()
            env={'GITHUB_ACTIONS':'true','RUNNER_OS':'Windows','RUNNER_TEMP':str(root)}
            with self.assertRaises(ValueError):
                with ci_debug_policy(env,root/'absent'):
                    raise ValueError('fixture interruption')
            self.assertEqual(registry.SetValueEx.call_args.args[1], 'wanxiang-chengwen.exe')
            self.assertEqual(registry.DeleteValue.call_args.args[1], 'wanxiang-chengwen.exe')
            registry.QueryValueEx.side_effect=None
            registry.QueryValueEx.return_value=('previous', 1)
            with ci_debug_policy(env,root/'absent'): pass
            self.assertEqual(registry.SetValueEx.call_args.args[-1], 'previous')
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

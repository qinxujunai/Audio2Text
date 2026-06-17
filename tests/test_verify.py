from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VerifyTestCase(unittest.TestCase):
    def test_verify_uses_explicit_py_compile_file_list(self) -> None:
        from scripts import verify

        self.assertTrue(verify.PYTHON_FILES)
        self.assertTrue(all("*" not in item for item in verify.PYTHON_FILES))
        self.assertIn("app/main.py", verify.PYTHON_FILES)
        self.assertIn("scripts/doctor.py", verify.PYTHON_FILES)
        self.assertIn("scripts/benchmark_transcription.py", verify.PYTHON_FILES)

    def test_verify_runs_daily_quality_gate(self) -> None:
        verify_script = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")

        self.assertIn('"doctor"', verify_script)
        self.assertIn('"py_compile"', verify_script)
        self.assertIn('"backend tests"', verify_script)
        self.assertIn('"frontend type check"', verify_script)
        self.assertIn('"frontend build"', verify_script)
        self.assertIn('"--backend-only"', verify_script)
        self.assertIn('"--transcribe-smoke-file"', verify_script)

    def test_verify_forces_utf8_subprocess_output(self) -> None:
        from scripts import verify

        env = verify._quality_gate_env()

        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_verify_reconfigures_parent_stdout(self) -> None:
        verify_script = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")

        self.assertIn("sys.stdout.reconfigure", verify_script)
        self.assertIn("_force_utf8_stdout()", verify_script)

    def test_transcribe_smoke_checks_cuda_when_configured(self) -> None:
        from scripts import verify

        self.assertIn('DEVICE == "cuda"', verify.TRANSCRIBE_SMOKE_SNIPPET)
        self.assertIn('payload.get("device") != "cuda"', verify.TRANSCRIBE_SMOKE_SNIPPET)
        self.assertIn('"Transcription smoke produced no segments"', verify.TRANSCRIBE_SMOKE_SNIPPET)
        self.assertIn("txt_file.exists()", verify.TRANSCRIBE_SMOKE_SNIPPET)
        self.assertIn("transcript_text", verify.TRANSCRIBE_SMOKE_SNIPPET)
        self.assertIn('"text_preview"', verify.TRANSCRIBE_SMOKE_SNIPPET)


if __name__ == "__main__":
    unittest.main()

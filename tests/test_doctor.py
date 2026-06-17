from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import doctor


class DoctorTestCase(unittest.TestCase):
    def test_check_imports_reports_missing_dependency(self) -> None:
        def fake_import(name: str):
            if name == "faster_whisper":
                raise ModuleNotFoundError("missing faster_whisper")
            return object()

        with patch.object(doctor, "REQUIRED_IMPORTS", ("fastapi", "faster_whisper")), patch.object(
            doctor.importlib,
            "import_module",
            side_effect=fake_import,
        ):
            checks = doctor._check_imports()

        self.assertEqual(
            checks,
            [
                {"name": "fastapi", "status": "ok", "detail": ""},
                {"name": "faster_whisper", "status": "missing", "detail": "missing faster_whisper"},
            ],
        )

    def test_run_doctor_keeps_preflight_warnings_visible(self) -> None:
        summary = {
            "workspace_dir": ".",
            "model_path": ".",
            "ffmpeg_path": ".",
            "playwright_browsers_dir": ".",
            "device": "cpu",
            "transcription_provider": "local_faster_whisper",
            "transcription_available": True,
            "compute_type": "int8",
            "beam_size": 1,
            "vad_filter": True,
            "model_path_source": "workspace_default",
            "ffmpeg_path_source": "workspace_default",
        }

        class FakePreflight:
            ok = True
            fatal_errors: list[str] = []
            warnings = ["browser profile empty"]

        with patch.object(doctor, "runtime_summary", return_value=summary), patch.object(
            doctor,
            "_check_imports",
            return_value=[],
        ), patch.object(doctor, "run_runtime_preflight", return_value=FakePreflight()):
            result = doctor.run_doctor()

        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], ["browser profile empty"])
        self.assertIn({"name": "runtime_preflight", "status": "ok", "detail": ""}, result["checks"])


if __name__ == "__main__":
    unittest.main()

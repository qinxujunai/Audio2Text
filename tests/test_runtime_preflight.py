from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import app.extractors as extractors
import app.runtime_preflight as runtime_preflight


class DummyYoutubeDL:
    captured_options: dict | None = None

    def __init__(self, options: dict) -> None:
        DummyYoutubeDL.captured_options = options

    def __enter__(self) -> DummyYoutubeDL:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
        target = Path(DummyYoutubeDL.captured_options["outtmpl"].replace("%(id)s", "sample").replace("%(ext)s", "m4a"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"audio")
        return {
            "requested_downloads": [
                {
                    "filepath": str(target)
                }
            ]
        }

    def prepare_filename(self, info: dict) -> str:
        return str(Path("sample.m4a"))


class RuntimePreflightTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "tests_runtime" / "preflight_runs" / uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.root.exists():
            for child in sorted(self.root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            self.root.rmdir()

    def test_local_provider_requires_model_directory(self) -> None:
        model_dir = self.root / "missing-model"
        ffmpeg_path = self.root / "ffmpeg" / "ffmpeg.exe"
        browsers_dir = self.root / "playwright"
        browsers_dir.mkdir(parents=True, exist_ok=True)
        browser_profile_dir = self.root / "browser-profile"
        browser_profile_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(runtime_preflight, "TRANSCRIPTION_PROVIDER", "local_faster_whisper"), patch.object(
            runtime_preflight, "MODEL_PATH", model_dir
        ), patch.object(runtime_preflight, "MODEL_PATH_IS_LEGACY_FALLBACK", False), patch.object(
            runtime_preflight, "FFMPEG_PATH", ffmpeg_path
        ), patch.object(runtime_preflight, "FFMPEG_PATH_SOURCE", "workspace_default"), patch.object(
            runtime_preflight, "PLAYWRIGHT_BROWSERS_DIR", browsers_dir
        ), patch.object(runtime_preflight, "BROWSER_PROFILE_DIR", browser_profile_dir
        ):
            result = runtime_preflight.run_runtime_preflight()

        self.assertTrue(any("模型目录不存在" in item for item in result.fatal_errors))
        self.assertTrue(any("Playwright Chromium" in item for item in result.warnings))
        self.assertTrue(any("浏览器会话目录当前为空" in item for item in result.warnings))
        self.assertTrue(any("ffmpeg" in item for item in result.warnings))

    def test_degraded_preview_allows_missing_local_model(self) -> None:
        model_dir = self.root / "missing-model"
        ffmpeg_path = self.root / "ffmpeg" / "ffmpeg.exe"
        browsers_dir = self.root / "playwright"
        (browsers_dir / "chromium-1234").mkdir(parents=True, exist_ok=True)
        browser_profile_dir = self.root / "browser-profile"
        browser_profile_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(runtime_preflight, "TRANSCRIPTION_PROVIDER", "local_faster_whisper"), patch.object(
            runtime_preflight, "ALLOW_DEGRADED_START", True
        ), patch.object(runtime_preflight, "MODEL_PATH", model_dir), patch.object(
            runtime_preflight, "MODEL_PATH_IS_LEGACY_FALLBACK", False
        ), patch.object(runtime_preflight, "FFMPEG_PATH", ffmpeg_path), patch.object(
            runtime_preflight, "FFMPEG_PATH_SOURCE", "workspace_default"
        ), patch.object(runtime_preflight, "PLAYWRIGHT_BROWSERS_DIR", browsers_dir), patch.object(
            runtime_preflight, "BROWSER_PROFILE_DIR", browser_profile_dir
        ):
            result = runtime_preflight.run_runtime_preflight()

        self.assertEqual(result.fatal_errors, [])
        self.assertGreater(len(result.warnings), 0)

    def test_openai_provider_requires_complete_configuration(self) -> None:
        ffmpeg_path = self.root / "ffmpeg" / "ffmpeg.exe"
        browsers_dir = self.root / "playwright"
        browsers_dir.mkdir(parents=True, exist_ok=True)
        browser_profile_dir = self.root / "browser-profile"
        browser_profile_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(runtime_preflight, "TRANSCRIPTION_PROVIDER", "openai_compatible"), patch.object(
            runtime_preflight, "OPENAI_COMPATIBLE_BASE_URL", ""
        ), patch.object(runtime_preflight, "OPENAI_COMPATIBLE_API_KEY", ""), patch.object(
            runtime_preflight, "OPENAI_COMPATIBLE_MODEL", ""
        ), patch.object(runtime_preflight, "FFMPEG_PATH", ffmpeg_path), patch.object(
            runtime_preflight, "FFMPEG_PATH_SOURCE", "workspace_default"
        ), patch.object(runtime_preflight, "PLAYWRIGHT_BROWSERS_DIR", browsers_dir), patch.object(
            runtime_preflight, "BROWSER_PROFILE_DIR", browser_profile_dir
        ):
            result = runtime_preflight.run_runtime_preflight()

        self.assertTrue(any("OpenAI-compatible 转写模式未配置完整" in item for item in result.fatal_errors))

    def test_legacy_model_fallback_is_warning_only(self) -> None:
        model_dir = self.root / "legacy-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = self.root / "ffmpeg" / "ffmpeg.exe"
        ffmpeg_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_path.write_text("binary", encoding="utf-8")
        browsers_dir = self.root / "playwright"
        (browsers_dir / "chromium-1234").mkdir(parents=True, exist_ok=True)
        browser_profile_dir = self.root / "browser-profile"
        (browser_profile_dir / "Default").mkdir(parents=True, exist_ok=True)
        (browser_profile_dir / "Local State").write_text("{}", encoding="utf-8")

        with patch.object(runtime_preflight, "TRANSCRIPTION_PROVIDER", "local_faster_whisper"), patch.object(
            runtime_preflight, "MODEL_PATH", model_dir
        ), patch.object(runtime_preflight, "MODEL_PATH_IS_LEGACY_FALLBACK", True), patch.object(
            runtime_preflight, "FFMPEG_PATH", ffmpeg_path
        ), patch.object(runtime_preflight, "FFMPEG_PATH_SOURCE", "workspace_default"), patch.object(
            runtime_preflight, "PLAYWRIGHT_BROWSERS_DIR", browsers_dir
        ), patch.object(runtime_preflight, "BROWSER_PROFILE_DIR", browser_profile_dir
        ):
            result = runtime_preflight.run_runtime_preflight()

        self.assertEqual(result.fatal_errors, [])
        self.assertTrue(any("legacy 模型目录" in item for item in result.warnings))
        self.assertFalse(any("浏览器会话目录当前为空" in item for item in result.warnings))

    def test_empty_browser_profile_warns_high_volatility_platforms(self) -> None:
        model_dir = self.root / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = self.root / "ffmpeg" / "ffmpeg.exe"
        ffmpeg_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_path.write_text("binary", encoding="utf-8")
        browsers_dir = self.root / "playwright"
        (browsers_dir / "chromium-1234").mkdir(parents=True, exist_ok=True)
        browser_profile_dir = self.root / "browser-profile"
        browser_profile_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(runtime_preflight, "TRANSCRIPTION_PROVIDER", "local_faster_whisper"), patch.object(
            runtime_preflight, "MODEL_PATH", model_dir
        ), patch.object(runtime_preflight, "MODEL_PATH_IS_LEGACY_FALLBACK", False), patch.object(
            runtime_preflight, "FFMPEG_PATH", ffmpeg_path
        ), patch.object(runtime_preflight, "FFMPEG_PATH_SOURCE", "workspace_default"), patch.object(
            runtime_preflight, "PLAYWRIGHT_BROWSERS_DIR", browsers_dir
        ), patch.object(runtime_preflight, "BROWSER_PROFILE_DIR", browser_profile_dir):
            result = runtime_preflight.run_runtime_preflight()

        self.assertEqual(result.fatal_errors, [])
        self.assertTrue(any("高波动平台将优先只走公开链路" in item for item in result.warnings))

    def test_explicit_cuda_requires_runtime_dlls_on_windows(self) -> None:
        model_dir = self.root / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = self.root / "ffmpeg" / "ffmpeg.exe"
        ffmpeg_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_path.write_text("binary", encoding="utf-8")
        browsers_dir = self.root / "playwright"
        (browsers_dir / "chromium-1234").mkdir(parents=True, exist_ok=True)
        browser_profile_dir = self.root / "browser-profile"
        (browser_profile_dir / "Default").mkdir(parents=True, exist_ok=True)
        (browser_profile_dir / "Default" / "Preferences").write_text("{}", encoding="utf-8")

        with patch.object(runtime_preflight, "TRANSCRIPTION_PROVIDER", "local_faster_whisper"), patch.object(
            runtime_preflight, "DEVICE", "cuda"
        ), patch.object(runtime_preflight, "MODEL_PATH", model_dir), patch.object(
            runtime_preflight, "MODEL_PATH_IS_LEGACY_FALLBACK", False
        ), patch.object(runtime_preflight, "FFMPEG_PATH", ffmpeg_path), patch.object(
            runtime_preflight, "FFMPEG_PATH_SOURCE", "workspace_default"
        ), patch.object(runtime_preflight, "PLAYWRIGHT_BROWSERS_DIR", browsers_dir), patch.object(
            runtime_preflight, "BROWSER_PROFILE_DIR", browser_profile_dir
        ), patch.object(runtime_preflight, "_find_runtime_file", return_value=None), patch.object(
            runtime_preflight.sys, "platform", "win32"
        ):
            result = runtime_preflight.run_runtime_preflight()

        self.assertTrue(any("CUDA 转写" in item and "cublas64_12.dll" in item for item in result.fatal_errors))

    def test_auto_device_does_not_require_cuda_runtime_dlls(self) -> None:
        result = runtime_preflight.PreflightResult()

        with patch.object(runtime_preflight, "DEVICE", "auto"), patch.object(
            runtime_preflight, "_find_runtime_file", return_value=None
        ), patch.object(runtime_preflight.sys, "platform", "win32"):
            runtime_preflight._check_cuda_runtime(result)

        self.assertEqual(result.fatal_errors, [])

    def test_ytdlp_download_uses_configured_ffmpeg_location(self) -> None:
        capture_dir = self.root / "capture"
        capture_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = self.root / "ffmpeg" / "ffmpeg.exe"

        with patch.object(extractors, "FFMPEG_PATH", ffmpeg_path), patch.object(
            extractors, "YoutubeDL", DummyYoutubeDL
        ):
            media_path = extractors._download_media_with_ytdlp(
                "https://example.com/video",
                capture_dir,
                content_type="video",
            )

        self.assertEqual(str(media_path), str(capture_dir / "sample.m4a"))
        self.assertIsNotNone(DummyYoutubeDL.captured_options)
        self.assertEqual(DummyYoutubeDL.captured_options["ffmpeg_location"], str(ffmpeg_path))
        self.assertIn("[height<=1080]", DummyYoutubeDL.captured_options["format"])


if __name__ == "__main__":
    unittest.main()

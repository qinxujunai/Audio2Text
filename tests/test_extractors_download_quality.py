from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.extractors as extractors


class ExtractorDownloadQualityTestCase(unittest.TestCase):
    def test_video_download_format_prefers_separate_browser_friendly_streams(self) -> None:
        selector = extractors._download_format_selector("video")

        self.assertIn("bestvideo[vcodec^=avc1][ext=mp4][height<=1080]+bestaudio[ext=m4a]", selector)
        self.assertIn("bestvideo*[height<=1080]+bestaudio", selector)
        self.assertTrue(
            selector.index("bestvideo[vcodec^=avc1][ext=mp4][height<=1080]+bestaudio[ext=m4a]") < selector.index("best[ext=mp4]")
        )
        self.assertTrue(selector.endswith("best[ext=mp4]/best"))

    def test_youtube_delivery_prefers_stable_720p_before_1080p(self) -> None:
        selector = extractors._download_format_selector_for_platform("video", "youtube")

        self.assertIn("bestvideo[vcodec^=avc1][ext=mp4][height<=720]+bestaudio[ext=m4a]", selector)
        self.assertIn("bestvideo[vcodec^=avc1][ext=mp4][height<=1080]+bestaudio[ext=m4a]", selector)
        self.assertLess(selector.index("height<=720"), selector.index("height<=1080"))

    def test_youtube_media_timeout_uses_muxed_fallback_before_deadline(self) -> None:
        calls: list[dict] = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise extractors.DownloadError("primary DASH stalled")
            target = kwargs["capture_dir"] / "demo.mp4"
            target.write_bytes(b"media")
            return {"id": "demo"}

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            extractors, "_run_ytdlp_media_download", side_effect=fake_download
        ), patch.object(extractors, "YTDLP_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", 90.0):
            selection = extractors._download_media_selection_with_ytdlp(
                "https://www.youtube.com/watch?v=demo",
                Path(temp_dir),
                platform="youtube",
                content_type="video",
            )

        self.assertEqual(selection.path.name, "demo.mp4")
        self.assertEqual(len(calls), 2)
        self.assertIn("bestvideo", calls[0]["options"]["format"])
        self.assertIn("best[ext=mp4]", calls[1]["options"]["format"])
        self.assertGreaterEqual(calls[0]["timeout_seconds"], 30.0)
        self.assertGreaterEqual(calls[1]["timeout_seconds"], 30.0)

    def test_audio_download_format_prefers_m4a_then_bestaudio(self) -> None:
        self.assertEqual(extractors._download_format_selector("audio"), "bestaudio[ext=m4a]/bestaudio/best")

    def test_download_selection_trace_detail_includes_quality_fields(self) -> None:
        selection = extractors.DownloadSelection(
            path=Path("sample.mp4"),
            format_id="137+140",
            resolution="1920x1080",
            container="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="h264",
            audio_codec="aac",
        )

        self.assertEqual(
            selection.trace_detail(),
            "format_id=137+140, resolution=1920x1080, container=mov,mp4,m4a,3gp,3g2,mj2, video_codec=h264, audio_codec=aac",
        )

    def test_extract_with_ytdlp_records_download_quality_trace(self) -> None:
        resolved = SimpleNamespace(
            platform="youtube",
            content_type="video",
            normalized_url="https://example.com/watch?v=demo",
        )
        info = {
            "title": "demo",
            "webpage_url": resolved.normalized_url,
            "duration": 12,
            "description": "",
            "language": "en",
        }
        selection = extractors.DownloadSelection(
            path=Path("sample.mp4"),
            format_id="137+140",
            resolution="1920x1080",
            container="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="h264",
            audio_codec="aac",
        )

        with patch.object(extractors, "_ytdlp_metadata", return_value=(info, [])), patch.object(
            extractors, "_select_subtitle_track", return_value=(None, "none", "")
        ), patch.object(
            extractors, "_download_media_selection_with_ytdlp", return_value=selection
        ):
            outcome = extractors.extract_with_ytdlp(resolved, Path("."))

        self.assertEqual(outcome.media_file_path, "sample.mp4")
        self.assertTrue(any("format_id=137+140" in (entry.detail or "") for entry in outcome.provider_traces))


if __name__ == "__main__":
    unittest.main()

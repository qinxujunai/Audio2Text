from __future__ import annotations

import unittest
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

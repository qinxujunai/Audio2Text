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

    def _long_video(self, duration):
        resolved = SimpleNamespace(platform="bilibili", content_type="video", normalized_url="https://www.bilibili.com/video/BV1demo/")
        info = {"title": "长访谈", "webpage_url": resolved.normalized_url, "duration": duration, "description": ""}
        return resolved, info

    def test_long_video_downloads_audio_first_and_defers_the_picture(self) -> None:
        resolved, info = self._long_video(7365)
        calls = []

        def download(url, capture_dir, **kwargs):
            calls.append(kwargs)
            return extractors.DownloadSelection(path=Path("audio.m4a"))

        with patch.object(extractors, "_ytdlp_metadata", return_value=(info, [])), patch.object(
            extractors, "_select_subtitle_track", return_value=(None, "none", "")
        ), patch.object(extractors, "_download_media_selection_with_ytdlp", side_effect=download):
            outcome = extractors.extract_with_ytdlp(resolved, Path("."))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["content_type"], "audio")
        self.assertEqual(calls[0]["format_selector"], extractors.AUDIO_ONLY_FORMAT)
        self.assertGreater(calls[0]["timeout_seconds"], extractors.YTDLP_MEDIA_DOWNLOAD_TIMEOUT_SECONDS)
        self.assertEqual(outcome.media_file_path, "audio.m4a")
        self.assertEqual(outcome.deferred_video["platform"], "ytdlp")
        self.assertEqual(outcome.deferred_video["url"], resolved.normalized_url)
        self.assertIn("audio_first", outcome.strategy)

    def test_short_video_still_downloads_the_whole_file(self) -> None:
        resolved, info = self._long_video(90)
        with patch.object(extractors, "_ytdlp_metadata", return_value=(info, [])), patch.object(
            extractors, "_select_subtitle_track", return_value=(None, "none", "")
        ), patch.object(
            extractors, "_download_media_selection_with_ytdlp", return_value=extractors.DownloadSelection(path=Path("v.mp4"))
        ) as download:
            outcome = extractors.extract_with_ytdlp(resolved, Path("."))
        self.assertEqual(download.call_args.kwargs["content_type"], "video")
        self.assertIsNone(outcome.deferred_video)

    def test_audio_first_failure_falls_back_to_the_whole_video(self) -> None:
        resolved, info = self._long_video(7365)
        results = [extractors.DownloadError("audio blocked"), extractors.DownloadSelection(path=Path("v.mp4"))]

        def download(url, capture_dir, **kwargs):
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(extractors, "_ytdlp_metadata", return_value=(info, [])), patch.object(
            extractors, "_select_subtitle_track", return_value=(None, "none", "")
        ), patch.object(extractors, "_download_media_selection_with_ytdlp", side_effect=download):
            outcome = extractors.extract_with_ytdlp(resolved, Path("."))
        self.assertEqual(outcome.media_file_path, "v.mp4")
        self.assertIsNone(outcome.deferred_video)

    def test_download_budget_grows_with_length(self) -> None:
        base = extractors.YTDLP_MEDIA_DOWNLOAD_TIMEOUT_SECONDS
        self.assertEqual(extractors._media_download_budget(30, video=False), base)
        self.assertGreater(extractors._media_download_budget(11779, video=True), 2900)
        self.assertGreater(extractors._media_download_budget(11779, video=False), 600)

    def test_deferred_ytdlp_video_is_moved_next_to_the_capture(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)

            def download(url, work_dir, **kwargs):
                path = work_dir / "BV1demo.mp4"
                path.write_bytes(b"video")
                self.assertEqual(kwargs["content_type"], "video")
                return extractors.DownloadSelection(path=path)

            with patch.object(extractors, "_download_media_selection_with_ytdlp", side_effect=download):
                result = extractors.complete_deferred_ytdlp_video(
                    {"platform": "ytdlp", "url": "https://example.com/v", "duration_seconds": 7365}, root
                )
            self.assertEqual(result, root / "source_video.mp4")
            self.assertEqual(result.read_bytes(), b"video")
            self.assertFalse((root / "deferred-video").exists())


if __name__ == "__main__":
    unittest.main()

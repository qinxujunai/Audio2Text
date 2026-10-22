"""Text first, original video afterwards.

A long Bilibili video's picture stream can be hundreds of megabytes while its audio is a few
dozen. Transcription needs only the audio, so extraction downloads the audio track, the text is
delivered as soon as it is ready, and the picture stream is fetched in the background and merged.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.extractors as extractors
import app.pipeline as pipeline
from app.extractors import ExtractionError
from app.schemas import ResultDocumentModel, SourceMetaModel

FFMPEG = shutil.which("ffmpeg")


def _media(path: Path, *, video: bool) -> Path:
    """A one-second real media file so artifact code can probe it."""
    if video:
        args = ["-f", "lavfi", "-i", "testsrc=size=160x90:rate=10", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    else:
        args = ["-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:a", "aac", "-f", "mp4"]
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args, str(path)], check=True)
    return path


def _bilibili_payloads(*, with_audio: bool = True):
    view = {"data": {"title": "长视频", "desc": "", "owner": {"name": "UP"}, "pic": "", "aid": 1, "cid": 2,
                     "duration": 3600, "pages": [{"cid": 2, "part": "", "duration": 3600}]}}
    player = {"data": {}}
    dash = {"data": {"dash": {
        "video": [{"baseUrl": "https://upos.example/v1080.m4s", "height": 1080, "bandwidth": 2_000_000}],
        "audio": [{"baseUrl": "https://upos.example/a.m4s", "bandwidth": 128_000}] if with_audio else [],
    }}}

    def api_json(_client, url, reason_code=""):
        if "web-interface/view" in url:
            return view
        if "player/wbi" in url:
            return player
        if "fnval=16" in url:
            return dash
        return {"data": {}}

    return api_json


class BilibiliAudioFirstTestCase(unittest.TestCase):
    def test_extraction_downloads_only_the_audio_and_defers_the_video(self) -> None:
        downloaded: list[str] = []

        def fake_download(url, path, **_kwargs):
            downloaded.append(url)
            Path(path).write_bytes(b"audio")

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            extractors, "_bilibili_api_json", side_effect=_bilibili_payloads()
        ), patch.object(extractors, "_download_file", side_effect=fake_download):
            resolved = SimpleNamespace(normalized_url="https://www.bilibili.com/video/BV1xx411c7mD")
            outcome = extractors.extract_bilibili_direct(resolved, Path(tmp))

            self.assertEqual(downloaded, ["https://upos.example/a.m4s"], "the picture stream is not fetched yet")
            self.assertTrue(outcome.media_file_path.endswith("bilibili_audio.m4s"))
            self.assertTrue(outcome.needs_transcription)
            self.assertIn("audio_first", outcome.strategy)
            self.assertEqual(outcome.deferred_video["platform"], "bilibili")
            self.assertEqual(outcome.deferred_video["audio_path"], outcome.media_file_path)
            self.assertEqual((outcome.deferred_video["aid"], outcome.deferred_video["cid"]), (1, 2))

    def test_without_a_dash_audio_track_it_falls_back_to_the_whole_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            extractors, "_bilibili_api_json", side_effect=_bilibili_payloads(with_audio=False)
        ), patch.object(
            extractors, "_download_bilibili_media", return_value=(Path(tmp) / "bilibili_source.mp4", [])
        ) as whole:
            (Path(tmp) / "bilibili_source.mp4").write_bytes(b"video")
            resolved = SimpleNamespace(normalized_url="https://www.bilibili.com/video/BV1xx411c7mD")
            outcome = extractors.extract_bilibili_direct(resolved, Path(tmp))

            whole.assert_called_once()
            self.assertIsNone(outcome.deferred_video)
            self.assertIn("media_direct", outcome.strategy)

    def test_deferred_video_requests_a_fresh_address_and_merges_with_the_saved_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "bilibili_audio.m4s"
            audio.write_bytes(b"audio")
            spec = {"platform": "bilibili", "aid": 1, "cid": 2, "bvid": "BV1xx411c7mD", "audio_path": str(audio)}
            fetched: list[str] = []
            with patch.object(extractors, "_bilibili_api_json", side_effect=_bilibili_payloads()), patch.object(
                extractors, "_download_file", side_effect=lambda url, path, **_: (fetched.append(url), Path(path).write_bytes(b"v"))
            ), patch.object(extractors, "_merge_media_streams", side_effect=lambda v, a, o: (o.write_bytes(b"mp4"), o)[1]) as merge:
                merged = extractors.complete_deferred_bilibili_video(spec, Path(tmp))

            self.assertEqual(fetched, ["https://upos.example/v1080.m4s"])
            self.assertEqual(merged, Path(tmp) / "bilibili_source.mp4")
            self.assertEqual(merge.call_args.args[1], audio)


def _video_capture(capture_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=capture_id,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        title="长视频",
        asset_preparation_pending=True,
        status="done",
        result_state="text_ready",
        source=SourceMetaModel(platform="bilibili", content_type="video"),
        processing=pipeline.ProcessingStateModel(),
        result=ResultDocumentModel(primary_text="转写好的文字"),
    )


@unittest.skipUnless(FFMPEG, "ffmpeg is required to build sample media")
class PipelineTextFirstTestCase(unittest.TestCase):
    def test_audio_only_media_is_never_delivered_as_the_original_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, "ARTIFACTS_DIR", Path(tmp)):
            capture = _video_capture("capture-text-first")
            audio = _media(Path(tmp) / "audio.m4a", video=False)
            with patch.object(pipeline, "FFMPEG_PATH", Path(FFMPEG)):
                result = pipeline._persist_result_artifacts(
                    capture, capture.result, media_file_path=str(audio), video_deferred=True, include_images=False
                )
            kinds = {artifact.type for artifact in result.artifacts}
            self.assertNotIn("source_media", kinds)
            self.assertNotIn("preview_media", kinds)
            self.assertIn("source_audio", kinds, "the audio is still offered while the video downloads")

    def test_background_completion_adds_the_original_video_and_clears_the_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, "ARTIFACTS_DIR", Path(tmp)):
            capture = _video_capture("capture-background-video")
            pipeline._save_deferred_video(capture.id, {"platform": "bilibili", "aid": 1, "cid": 2, "bvid": "BV1", "audio_path": "a"})
            video = _media(Path(tmp) / "merged.mp4", video=True)
            updates: list[dict] = []

            def update(_id, **fields):
                updates.append(fields)
                for key, value in fields.items():
                    if value is not None:
                        setattr(capture, key, value)
                return capture

            with patch.object(pipeline.repository, "get", return_value=capture), patch.object(
                pipeline.repository, "update", side_effect=update
            ), patch.object(pipeline, "complete_deferred_bilibili_video", return_value=video):
                pipeline._prepare_image_assets_in_background(capture.id)

            self.assertFalse(capture.asset_preparation_pending)
            self.assertEqual(capture.result_state, "complete")
            self.assertIn("source_media", {artifact.type for artifact in capture.result.artifacts})
            self.assertFalse((Path(tmp) / capture.id / pipeline.DEFERRED_VIDEO_FILE).exists())
            self.assertTrue(any("正在后台下载原视频" in (f.get("processing").progress_detail or "") for f in updates if f.get("processing")))

    def test_a_failed_background_download_keeps_the_text_and_says_what_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, "ARTIFACTS_DIR", Path(tmp)):
            capture = _video_capture("capture-background-failure")
            pipeline._save_deferred_video(capture.id, {"platform": "bilibili", "aid": 1, "cid": 2, "bvid": "BV1", "audio_path": "a"})

            def update(_id, **fields):
                for key, value in fields.items():
                    if value is not None:
                        setattr(capture, key, value)
                return capture

            with patch.object(pipeline.repository, "get", return_value=capture), patch.object(
                pipeline.repository, "update", side_effect=update
            ), patch.object(
                pipeline,
                "complete_deferred_bilibili_video",
                side_effect=ExtractionError("download", "下载超时", reason_code="download_failed", retryable=True),
            ):
                pipeline._prepare_image_assets_in_background(capture.id)

            self.assertFalse(capture.asset_preparation_pending)
            self.assertEqual(capture.result.primary_text, "转写好的文字")
            self.assertIn("原视频暂未下载完成", capture.processing.progress_detail)


if __name__ == "__main__":
    unittest.main()

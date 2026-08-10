from __future__ import annotations

import queue
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import app.pipeline as pipeline
import app.store_fs as store_fs
from app.capture_diagnostics import CaptureDiagnostics, read_trace_records
from app.extractors import ExtractionOutcome
from app.schemas import ArtifactModel, ResultDocumentModel, SourceMetaModel


class PipelineAndStoreTestCase(unittest.TestCase):
    def test_capture_diagnostics_writes_stage_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            diagnostics = CaptureDiagnostics("cap-trace")
            diagnostics.path = Path(tmp_dir) / "capture_traces" / "cap-trace.jsonl"

            diagnostics.start("extract", provider="direct_provider")
            diagnostics.finish("extract", provider="direct_provider", strategy=["direct"])
            diagnostics.finish_capture(status="done", recovery_seconds=2.5)

            records = read_trace_records("cap-trace", logs_dir=Path(tmp_dir))

        self.assertEqual([record["event"] for record in records], ["stage_start", "stage_end", "capture_finished"])
        self.assertEqual(records[1]["stage"], "extract")
        self.assertIn("duration_seconds", records[1])
        self.assertEqual(records[-1]["status"], "done")
        self.assertEqual(records[-1]["recovery_seconds"], 2.5)

    def test_recovery_seconds_detects_worker_recovery_gap(self) -> None:
        capture = SimpleNamespace(
            processing=SimpleNamespace(
                trace_events=[
                    SimpleNamespace(at="2026-04-23 16:25:17", stage="extract", message="extract started"),
                    SimpleNamespace(at="2026-04-23 16:29:09", stage="queued", message="任务已恢复到等待队列。"),
                ]
            )
        )

        self.assertEqual(pipeline._recovery_seconds_from_trace_events(capture), 232.0)

    def test_clean_social_body_text_strips_topic_and_account_noise(self) -> None:
        cleaned = pipeline._clean_social_body_text(
            "装修人最爱的【选灯环节】\n细节介绍都在 live plog 里拉\n#氛围感灯具[话题]# #我家的灯长这样[话题]# @demo"
        )

        self.assertEqual(cleaned, "装修人最爱的【选灯环节】\n细节介绍都在 live plog 里拉")

    def test_clean_display_title_shrinks_social_caption_to_first_sentence(self) -> None:
        cleaned = pipeline._clean_display_title(
            "第一句已经足够作为标题。这里继续补充很多发布文案，还会继续往下讲 #话题# @demo",
            platform="douyin",
        )

        self.assertEqual(cleaned, "第一句已经足够作为标题。")

    def test_clean_display_title_keeps_normal_video_title(self) -> None:
        cleaned = pipeline._clean_display_title("How to Build a Faster Whisper Pipeline", platform="youtube")

        self.assertEqual(cleaned, "How to Build a Faster Whisper Pipeline")

    def test_resolve_display_title_marks_social_caption_as_synthetic(self) -> None:
        resolved = pipeline._resolve_display_title(
            "Opening sentence works as title. This continues with the real body and more context #topic @demo",
            platform="douyin",
        )

        self.assertEqual(resolved.text, "Opening sentence works as title.")
        self.assertEqual(resolved.kind, "synthetic")

    def test_result_title_does_not_fallback_to_source_description(self) -> None:
        resolved = pipeline._result_title(
            capture=SimpleNamespace(title=""),
            source=SourceMetaModel(
                platform="xiaohongshu",
                content_type="video",
                description="这是一大段页面文案，不应该再回退成展示标题。",
            ),
            extraction=ExtractionOutcome(platform="xiaohongshu", content_type="video", title=""),
        )

        self.assertEqual(resolved, "")

    def test_drop_title_from_body_removes_repeated_prefix_line(self) -> None:
        body = "第一句已经足够作为标题。这里才是正文开头\n第二段正文"

        dropped = pipeline._drop_title_from_body("第一句已经足够作为标题。", body)

        self.assertEqual(dropped, "这里才是正文开头\n第二段正文")

    def test_drop_title_from_body_keeps_inline_opening_sentence_for_synthetic_title(self) -> None:
        body = "Opening sentence works as title. This continues with the real body and more context."

        dropped = pipeline._drop_title_from_body(
            "Opening sentence works as title.",
            body,
            title_kind="synthetic",
        )

        self.assertEqual(dropped, body)

    def test_compose_txt_text_keeps_opening_sentence_when_title_is_only_display_copy(self) -> None:
        body = "Opening sentence works as title. This continues with the real body and more context."

        composed = pipeline._compose_txt_text("Opening sentence works as title.", body)

        self.assertEqual(
            composed,
            "Opening sentence works as title.\n\nOpening sentence works as title. This continues with the real body and more context.",
        )

    def test_merge_result_keeps_body_opening_sentence_for_synthetic_social_title(self) -> None:
        capture = SimpleNamespace(title="")
        source = SourceMetaModel(platform="douyin", content_type="video")
        caption = "Opening sentence works as title. This continues with the real body and more context."

        result = pipeline._merge_result(
            capture,
            source,
            ExtractionOutcome(
                platform="douyin",
                content_type="video",
                title=caption,
                subtitle_text=caption,
            ),
            None,
        )

        self.assertEqual(result.primary_text, caption)
        self.assertEqual(result.subtitle_text, caption)

    def test_drop_title_from_body_removes_repeated_prefix_line(self) -> None:
        body = "第一句已经足够作为标题。这里才是正文开头\n第二段正文"

        dropped = pipeline._drop_title_from_body("第一句已经足够作为标题。", body)

        self.assertEqual(dropped, body)

    def test_drop_title_from_body_removes_standalone_title_line(self) -> None:
        body = "Sample title\n\nBody preview"

        dropped = pipeline._drop_title_from_body("Sample title", body)

        self.assertEqual(dropped, "Body preview")

    def test_update_processing_tracks_progress_fields(self) -> None:
        capture = SimpleNamespace(
            processing=pipeline.ProcessingStateModel(),
        )

        updated = pipeline._update_processing(
            capture,
            current_stage="compose",
            progress_percent=85,
            progress_detail="已拿到可用字幕，正在跳过转写并整理结果。",
        )

        self.assertEqual(updated.current_stage, "compose")
        self.assertEqual(updated.progress_percent, 85)
        self.assertEqual(updated.progress_detail, "已拿到可用字幕，正在跳过转写并整理结果。")

    def test_compose_stage_before_done_does_not_report_100_percent(self) -> None:
        capture = SimpleNamespace(
            processing=pipeline.ProcessingStateModel(),
        )

        updated = pipeline._update_processing(
            capture,
            current_stage="compose",
            progress_percent=96,
            progress_detail="正文已整理完成，正在生成交付文件。",
        )

        self.assertEqual(updated.current_stage, "compose")
        self.assertEqual(updated.progress_percent, 96)
        self.assertNotEqual(updated.progress_percent, 100)

    def test_enqueue_capture_deduplicates_same_capture(self) -> None:
        local_queue: queue.Queue[str] = queue.Queue()

        with patch.object(pipeline, "capture_queue", local_queue), patch.object(
            pipeline.repository,
            "get",
            return_value=SimpleNamespace(status="queued"),
        ), patch.object(pipeline, "ensure_worker_started", lambda: None):
            pipeline.queued_capture_ids.clear()
            pipeline.active_capture_ids.clear()

            pipeline.enqueue_capture("cap123")
            pipeline.enqueue_capture("cap123")

            self.assertEqual(local_queue.qsize(), 1)
            self.assertIn("cap123", pipeline.queued_capture_ids)

        pipeline.queued_capture_ids.clear()
        pipeline.active_capture_ids.clear()

    def test_clear_completed_captures_removes_external_storage_file(self) -> None:
        root = Path.cwd() / "tests_runtime" / "store_runs" / uuid4().hex
        captures_dir = root / "captures"
        artifacts_dir = root / "artifacts"
        external_dir = root / "external"

        for folder in [captures_dir, artifacts_dir, external_dir]:
            folder.mkdir(parents=True, exist_ok=True)

        external_file = external_dir / "input.mp3"
        external_file.write_bytes(b"abc")

        with patch.object(store_fs, "CAPTURES_DIR", captures_dir), patch.object(
            store_fs, "ARTIFACTS_DIR", artifacts_dir
        ), patch.object(store_fs, "CAPTURE_HISTORY_LIMIT", 10):
            store_fs.init_db()
            capture = store_fs.create_capture(
                input_type="file",
                source_platform="local_file",
                content_type="audio",
                file_name="input.mp3",
                storage_path=str(external_file),
            )
            store_fs.update_capture(capture.id, status="done")

            cleared = store_fs.clear_completed_captures()

        self.assertEqual(cleared, 1)
        self.assertFalse(external_file.exists())
        self.assertFalse((captures_dir / capture.id).exists())
        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_write_json_retries_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "capture.json"
            replace_attempts = {"count": 0}
            real_replace = store_fs.os.replace

            def flaky_replace(source: Path | str, destination: Path | str) -> None:
                replace_attempts["count"] += 1
                if replace_attempts["count"] < 3:
                    raise PermissionError("busy")
                real_replace(source, destination)

            with patch.object(store_fs.os, "replace", side_effect=flaky_replace):
                store_fs._write_json(target, {"id": "cap-busy", "status": "queued"})

            self.assertEqual(replace_attempts["count"], 3)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["id"], "cap-busy")
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_write_json_raises_when_atomic_replace_never_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "capture.json"
            target.write_text('{"id":"original"}', encoding="utf-8")

            with patch.object(store_fs.os, "replace", side_effect=PermissionError("busy")):
                with self.assertRaises(store_fs.CaptureStoreUnavailableError):
                    store_fs._write_json(target, {"id": "updated"})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["id"], "original")

    def test_read_json_retries_after_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "capture.json"
            target.write_text('{"id":"cap-read"}', encoding="utf-8")
            real_read_text = Path.read_text
            attempts = {"count": 0}

            def flaky_read_text(path: Path, *args, **kwargs):
                if path == target and attempts["count"] == 0:
                    attempts["count"] += 1
                    raise PermissionError("busy")
                return real_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", autospec=True, side_effect=flaky_read_text):
                payload = store_fs._read_json(target)

            self.assertEqual(payload["id"], "cap-read")
            self.assertEqual(attempts["count"], 1)

    def test_list_captures_skips_temporarily_unavailable_record(self) -> None:
        root = Path.cwd() / "tests_runtime" / "store_runs" / uuid4().hex
        captures_dir = root / "captures"
        artifacts_dir = root / "artifacts"
        captures_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(store_fs, "CAPTURES_DIR", captures_dir), patch.object(
            store_fs, "ARTIFACTS_DIR", artifacts_dir
        ), patch.object(store_fs, "CAPTURE_HISTORY_LIMIT", 10):
            store_fs.init_db()
            busy_capture = store_fs.create_capture(
                input_type="url",
                source_platform="youtube",
                content_type="video",
                url="https://example.com/busy",
            )
            healthy_capture = store_fs.create_capture(
                input_type="url",
                source_platform="wechat_article",
                content_type="article",
                url="https://example.com/ok",
            )
            real_read_json = store_fs._read_json

            def maybe_busy(path: Path):
                if path.parent.name == busy_capture.id:
                    raise store_fs.CaptureStoreUnavailableError("busy")
                return real_read_json(path)

            with patch.object(store_fs, "_read_json", side_effect=maybe_busy):
                captures = store_fs.list_captures(limit=10)

        self.assertEqual([capture.id for capture in captures], [healthy_capture.id])
        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_adds_source_media_for_video_capture(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        media_dir = root / "media"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        media_path = media_dir / "sample.mp4"
        media_path.write_bytes(b"video-bytes")

        capture = SimpleNamespace(
            id="cap-video",
            title="sample",
            url="https://example.com/video",
            source=SourceMetaModel(platform="youtube", content_type="video", canonical_url="https://example.com/video"),
        )
        result = ResultDocumentModel(primary_text="hello world")

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir):
            persisted = pipeline._persist_result_artifacts(capture, result, media_file_path=str(media_path))

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertIn("txt", artifact_types)
        self.assertIn("source_media", artifact_types)
        self.assertNotIn("md", artifact_types)
        source_media = next(artifact for artifact in persisted.artifacts if artifact.type == "source_media")
        self.assertEqual(source_media.mime_type, "video/mp4")
        self.assertEqual(Path(source_media.path), media_path)
        txt_path = artifacts_dir / capture.id / "capture.txt"
        self.assertEqual(txt_path.read_text(encoding="utf-8"), "sample\n\nhello world")

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_adds_preview_media_for_risky_video(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        media_dir = root / "media"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        media_path = media_dir / "sample.mp4"
        media_path.write_bytes(b"video-bytes")

        capture = SimpleNamespace(
            id="cap-preview",
            title="sample",
            url="https://example.com/video",
            source=SourceMetaModel(platform="bilibili", content_type="video", canonical_url="https://example.com/video"),
        )
        result = ResultDocumentModel(primary_text="hello world")

        def fake_preview(source_path: Path, target_path: Path) -> Path:
            self.assertEqual(source_path, media_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"preview-bytes")
            return target_path

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_source_media_needs_preview", return_value=True
        ), patch.object(pipeline, "_generate_preview_media", side_effect=fake_preview):
            persisted = pipeline._persist_result_artifacts(capture, result, media_file_path=str(media_path))

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertIn("source_media", artifact_types)
        self.assertIn("preview_media", artifact_types)
        preview_media = next(artifact for artifact in persisted.artifacts if artifact.type == "preview_media")
        self.assertEqual(preview_media.mime_type, "video/mp4")
        self.assertEqual((artifacts_dir / capture.id / "preview_media.mp4").read_bytes(), b"preview-bytes")

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_exports_source_audio_for_video(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        media_dir = root / "media"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        media_path = media_dir / "sample.mp4"
        media_path.write_bytes(b"video-with-audio")

        capture = SimpleNamespace(
            id="cap-video-audio",
            title="sample",
            url="https://example.com/video",
            source=SourceMetaModel(platform="bilibili", content_type="video", canonical_url="https://example.com/video"),
        )
        result = ResultDocumentModel(primary_text="hello world")
        captured_command: list[str] = []

        def fake_run(command, capture_output, text, check):
            captured_command.extend(command)
            (artifacts_dir / capture.id / "source_audio.m4a").write_bytes(b"audio-bytes")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_audio_codec_for_path", return_value="opus"
        ), patch.object(pipeline.subprocess, "run", side_effect=fake_run), patch.object(
            pipeline, "_source_media_needs_preview", return_value=False
        ):
            persisted = pipeline._persist_result_artifacts(capture, result, media_file_path=str(media_path))

        source_audio = next(artifact for artifact in persisted.artifacts if artifact.type == "source_audio")
        self.assertEqual(source_audio.mime_type, "audio/mp4")
        self.assertEqual(Path(source_audio.path), artifacts_dir / capture.id / "source_audio.m4a")
        self.assertIn("-vn", captured_command)
        self.assertIn("192k", captured_command)

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_preserves_source_audio_for_audio_capture(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        media_dir = root / "media"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        media_path = media_dir / "episode.m4a"
        media_path.write_bytes(b"audio")

        capture = SimpleNamespace(
            id="cap-audio",
            title="episode",
            url="https://example.com/episode",
            source=SourceMetaModel(platform="xiaoyuzhou", content_type="audio", canonical_url="https://example.com/episode"),
        )
        result = ResultDocumentModel(primary_text="hello world")

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_audio_codec_for_path", return_value="aac"
        ):
            persisted = pipeline._persist_result_artifacts(capture, result, media_file_path=str(media_path))

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertIn("source_audio", artifact_types)
        self.assertNotIn("source_media", artifact_types)
        source_audio = next(artifact for artifact in persisted.artifacts if artifact.type == "source_audio")
        self.assertEqual(Path(source_audio.path), media_path)

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_skips_preview_media_for_browser_friendly_video(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        media_dir = root / "media"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        media_path = media_dir / "sample.mp4"
        media_path.write_bytes(b"video-bytes")

        capture = SimpleNamespace(
            id="cap-friendly",
            title="sample",
            url="https://example.com/video",
            source=SourceMetaModel(platform="douyin", content_type="video", canonical_url="https://example.com/video"),
        )
        result = ResultDocumentModel(primary_text="hello world")

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_source_media_needs_preview", return_value=False
        ):
            persisted = pipeline._persist_result_artifacts(capture, result, media_file_path=str(media_path))

        self.assertNotIn("preview_media", [artifact.type for artifact in persisted.artifacts])

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_generate_preview_media_uses_higher_quality_h264_settings(self) -> None:
        root = Path.cwd() / "tests_runtime" / "preview_runs" / uuid4().hex
        source_path = root / "source.mkv"
        target_path = root / "preview.mp4"
        ffmpeg_path = root / "ffmpeg.exe"
        root.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"video-bytes")
        ffmpeg_path.write_bytes(b"ffmpeg")
        captured_command: list[str] = []

        def fake_run(command, capture_output, text, check):
            captured_command.extend(command)
            target_path.write_bytes(b"preview-bytes")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with patch.object(pipeline, "FFMPEG_PATH", ffmpeg_path), patch.object(
            pipeline.subprocess, "run", side_effect=fake_run
        ):
            generated = pipeline._generate_preview_media(source_path, target_path)

        self.assertEqual(generated, target_path)
        self.assertIn("libx264", captured_command)
        self.assertIn("fast", captured_command)
        self.assertIn("19", captured_command)
        self.assertIn("+faststart", captured_command)
        self.assertIn("high", captured_command)

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_video_artifact_delivery_trace_reports_preview_generation(self) -> None:
        result = ResultDocumentModel(
            primary_text="hello world",
            artifacts=[
                ArtifactModel(type="source_media", label="source", path="source.mp4", download_url="/source", mime_type="video/mp4"),
                ArtifactModel(type="preview_media", label="preview", path="preview.mp4", download_url="/preview", mime_type="video/mp4"),
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            pipeline,
            "_probe_media_delivery_profile",
            return_value=("mov,mp4,m4a,3gp,3g2,mj2", "h264", "aac", "1920x1080"),
        ):
            source_path = Path(temp_dir) / "source.mp4"
            source_path.write_bytes(b"video")
            traces = pipeline._video_artifact_delivery_traces(result, str(source_path))

        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].provider, "artifact_pipeline")
        self.assertIn("resolution=1920x1080", traces[0].detail)
        self.assertIn("preview_media=yes", traces[0].detail)

    def test_persist_result_artifacts_skips_source_media_for_non_video_capture(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        media_dir = root / "media"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        media_path = media_dir / "sample.mp4"
        media_path.write_bytes(b"video-bytes")

        capture = SimpleNamespace(
            id="cap-article",
            title="sample",
            url="https://example.com/post",
            source=SourceMetaModel(platform="wechat_article", content_type="article", canonical_url="https://example.com/post"),
        )
        result = ResultDocumentModel(primary_text="hello world")

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir):
            persisted = pipeline._persist_result_artifacts(capture, result, media_file_path=str(media_path))

        self.assertNotIn("source_media", [artifact.type for artifact in persisted.artifacts])

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_video_without_subtitle_or_transcript_fails_final_result_check(self) -> None:
        source = SourceMetaModel(platform="douyin", content_type="video")
        result = pipeline._merge_result(
            SimpleNamespace(title="sample"),
            source,
            ExtractionOutcome(
                platform="douyin",
                content_type="video",
                title="sample",
                notes_text="这是一段页面文案，不应该被当成视频正文。",
                description="这是一段页面文案，不应该被当成视频正文。",
            ),
            None,
        )

        self.assertEqual(result.primary_text, "")
        self.assertEqual(result.text_source, "none")
        with self.assertRaises(pipeline.ExtractionError):
            pipeline._ensure_final_result(source, result)

    def test_merge_result_sets_notice_when_video_text_ready_but_media_missing(self) -> None:
        result = pipeline._merge_result(
            SimpleNamespace(title="sample"),
            SourceMetaModel(platform="youtube", content_type="video"),
            ExtractionOutcome(
                platform="youtube",
                content_type="video",
                title="sample",
                subtitle_text="hello subtitle",
                subtitle_source="manual",
            ),
            None,
        )

        self.assertEqual(result.result_notice, "文本已整理完成，原视频暂未成功下载。")

    def test_persist_result_artifacts_writes_markdown_only_when_available(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        capture = SimpleNamespace(
            id="cap-md",
            title="Checklist",
            url="https://example.com/post",
            source=SourceMetaModel(platform="wechat_article", content_type="article", canonical_url="https://example.com/post"),
        )
        result = ResultDocumentModel(primary_text="item one\nitem two", markdown_text="- item one\n- item two")

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir):
            persisted = pipeline._persist_result_artifacts(capture, result)

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertIn("txt", artifact_types)
        self.assertIn("md", artifact_types)
        md_path = artifacts_dir / capture.id / "capture.md"
        self.assertEqual(md_path.read_text(encoding="utf-8"), "- item one\n- item two")

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_can_skip_image_mirroring_for_async_delivery(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        capture = SimpleNamespace(
            id="cap-image-async",
            title="Async note",
            url="https://example.com/image-note",
            source=SourceMetaModel(
                platform="xiaohongshu",
                content_type="image_article",
                canonical_url="https://example.com/image-note",
                image_urls=["https://example.com/image.jpg"],
                live_photo_video_urls=["https://example.com/image-live.mp4"],
            ),
        )
        result = ResultDocumentModel(primary_text="body text", primary_result_type="article")

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_download_image", side_effect=AssertionError("image download should not run when include_images=False")
        ), patch.object(
            pipeline,
            "_download_remote_asset",
            side_effect=AssertionError("live download should not run when include_images=False"),
        ):
            persisted = pipeline._persist_result_artifacts(capture, result, include_images=False)

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertIn("txt", artifact_types)
        self.assertNotIn("images_zip", artifact_types)
        self.assertFalse(any(item.startswith("image_") for item in artifact_types))

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_can_prepare_initial_image_batch_without_zip(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        capture = SimpleNamespace(
            id="cap-image-preview",
            title="Preview note",
            url="https://example.com/image-note",
            source=SourceMetaModel(
                platform="xiaohongshu",
                content_type="image_article",
                canonical_url="https://example.com/image-note",
                image_urls=[
                    "https://example.com/image-1.jpg",
                    "https://example.com/image-2.jpg",
                    "https://example.com/image-3.jpg",
                ],
                live_photo_video_urls=["", "", ""],
            ),
        )
        result = ResultDocumentModel(primary_text="body text", primary_result_type="article")

        def fake_download(url: str, target: Path, *, referer: str | None = None) -> None:
            target.write_bytes(url.encode("utf-8"))

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_download_image", side_effect=fake_download
        ):
            persisted = pipeline._persist_result_artifacts(
                capture,
                result,
                include_images=False,
                image_preview_limit=2,
            )

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertIn("txt", artifact_types)
        self.assertIn("image_01", artifact_types)
        self.assertIn("image_02", artifact_types)
        self.assertNotIn("image_03", artifact_types)
        self.assertNotIn("images_zip", artifact_types)

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_pairs_live_photo_assets(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        capture = SimpleNamespace(
            id="cap-live",
            title="Live note",
            url="https://example.com/live",
            source=SourceMetaModel(
                platform="xiaohongshu",
                content_type="image_article",
                canonical_url="https://example.com/live",
                image_urls=["https://example.com/image.jpg"],
                live_photo_video_urls=["https://example.com/image_live.mp4"],
            ),
        )
        result = ResultDocumentModel(primary_text="body text", primary_result_type="article")

        def fake_download(url: str, target_path: Path, **_: object) -> Path:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"asset")
            return target_path

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_download_image", side_effect=fake_download
        ), patch.object(pipeline, "_download_remote_asset", side_effect=fake_download):
            persisted = pipeline._persist_result_artifacts(capture, result)

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertIn("image_01", artifact_types)
        self.assertIn("image_live_01", artifact_types)
        self.assertIn("images_zip", artifact_types)

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_persist_result_artifacts_dedupes_identical_images_by_content(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        capture = SimpleNamespace(
            id="cap-wechat-dedupe",
            title="Wechat note",
            url="https://example.com/wechat",
            source=SourceMetaModel(
                platform="wechat_article",
                content_type="image_article",
                canonical_url="https://example.com/wechat",
                image_urls=[
                    "https://example.com/cover.jpg",
                    "https://example.com/body-first.jpg",
                    "https://example.com/body-second.jpg",
                ],
                live_photo_video_urls=[],
            ),
        )
        result = ResultDocumentModel(primary_text="body text", primary_result_type="article")

        def fake_download(url: str, target_path: Path, **_: object) -> Path:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if "body-second" in url:
                target_path.write_bytes(b"third-image")
            else:
                target_path.write_bytes(b"same-image")
            return target_path

        progress_updates: list[tuple[int, str]] = []

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_download_image", side_effect=fake_download
        ), patch.object(
            pipeline,
            "_image_content_dedupe_key",
            side_effect=["visual:same", "visual:same", "visual:third"],
        ):
            persisted = pipeline._persist_result_artifacts(
                capture,
                result,
                progress_callback=lambda percent, detail: progress_updates.append((percent, detail)),
            )

        artifact_types = [artifact.type for artifact in persisted.artifacts]
        self.assertEqual([item for item in artifact_types if item.startswith("image_")], ["image_01", "image_03"])
        self.assertIn("images_zip", artifact_types)
        self.assertTrue(any("正在下载图片" in detail for _, detail in progress_updates))
        self.assertTrue(any("正在打包下载文件" in detail for _, detail in progress_updates))

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import queue
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import av

import app.pipeline as pipeline
import app.store_fs as store_fs
from app.capture_diagnostics import CaptureDiagnostics, read_trace_records
from app.extractors import ExtractionError, ExtractionOutcome
from app.schemas import ArtifactModel, ResultDocumentModel, SourceMetaModel


class PipelineAndStoreTestCase(unittest.TestCase):
    def test_transcription_provider_network_error_is_structured_and_retryable(self) -> None:
        error = pipeline._transcription_error_from_exception(
            RuntimeError("HTTPSConnectionPool: connection reset by peer")
        )

        self.assertEqual(error.stage, "transcribe")
        self.assertEqual(error.reason_code, "transcribe_network_failed")
        self.assertTrue(error.retryable)

    def test_transcription_runtime_unavailable_is_actionable_without_retry_loop(self) -> None:
        error = pipeline._transcription_error_from_exception(
            RuntimeError("轻量转写模型尚未准备完成。")
        )

        self.assertEqual(error.reason_code, "transcribe_runtime_unavailable")
        self.assertFalse(error.retryable)
        self.assertIn("转写运行时", pipeline._public_error_message_with_reason("transcribe", error.reason_code))

    def test_capture_store_never_persists_whole_capture_failed_state(self) -> None:
        root = Path.cwd() / "tests_runtime" / "store_runs" / uuid4().hex
        captures_dir = root / "captures"
        artifacts_dir = root / "artifacts"
        captures_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        try:
            with patch.object(store_fs, "CAPTURES_DIR", captures_dir), patch.object(
                store_fs, "ARTIFACTS_DIR", artifacts_dir
            ):
                capture = store_fs.create_capture(
                    input_type="url",
                    source_platform="generic_web",
                    content_type="webpage",
                    url="https://example.com/storage-boundary",
                )
                processing = capture.processing.model_copy(
                    update={"current_stage": "failed", "progress_percent": 0}
                )
                updated = store_fs.update_capture(
                    capture.id,
                    status="failed",
                    result_state="failed",
                    error_stage="extract",
                    error_message="需要用户重新选择来源。",
                    processing=processing,
                )

                self.assertIsNotNone(updated)
                self.assertEqual(updated.status, "needs_user_action")
                self.assertEqual(updated.result_state, "awaiting_user")
                self.assertEqual(updated.processing.current_stage, "awaiting_user")
                self.assertEqual(updated.processing.progress_percent, 18)
                persisted = store_fs.get_capture(capture.id)
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted.status, "needs_user_action")
                self.assertEqual(persisted.result_state, "awaiting_user")
        finally:
            for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            root.rmdir()

    def test_capture_store_repairs_legacy_failed_record_on_partial_update(self) -> None:
        root = Path.cwd() / "tests_runtime" / "store_runs" / uuid4().hex
        captures_dir = root / "captures"
        artifacts_dir = root / "artifacts"
        captures_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        try:
            with patch.object(store_fs, "CAPTURES_DIR", captures_dir), patch.object(
                store_fs, "ARTIFACTS_DIR", artifacts_dir
            ):
                capture = store_fs.create_capture(
                    input_type="url",
                    source_platform="generic_web",
                    content_type="webpage",
                    url="https://example.com/legacy-partial-update",
                )
                # Simulate a pre-migration file written by an older worker.
                store_fs.update_capture(
                    capture.id,
                    status="failed",
                    result_state="failed",
                    processing=capture.processing.model_copy(
                        update={"current_stage": "failed", "progress_percent": 0}
                    ),
                )

                updated = store_fs.update_capture(capture.id, title="仍可恢复的内容")

                self.assertIsNotNone(updated)
                self.assertEqual(updated.status, "needs_user_action")
                self.assertEqual(updated.result_state, "awaiting_user")
                self.assertEqual(updated.processing.current_stage, "awaiting_user")
                self.assertGreaterEqual(updated.processing.progress_percent, 18)
                persisted = store_fs.get_capture(capture.id)
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted.status, "needs_user_action")
        finally:
            for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            root.rmdir()

    def test_capture_update_keeps_concurrent_fields_from_losing_each_other(self) -> None:
        root = Path.cwd() / "tests_runtime" / "store_runs" / uuid4().hex
        captures_dir = root / "captures"
        artifacts_dir = root / "artifacts"
        captures_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(store_fs, "CAPTURES_DIR", captures_dir), patch.object(
            store_fs, "ARTIFACTS_DIR", artifacts_dir
        ):
            capture = store_fs.create_capture(
                input_type="url",
                source_platform="wechat_article",
                content_type="article",
                url="https://example.com/concurrent",
            )
            target = store_fs._capture_path(capture.id)
            real_read_json = store_fs._read_json
            first_read = threading.Event()
            release_first = threading.Event()
            second_read = threading.Event()
            allow_second = threading.Event()
            first_call = {"value": True}

            def coordinated_read(path: Path, **kwargs):
                payload = real_read_json(path, **kwargs)
                if path != target:
                    return payload
                if first_call["value"]:
                    first_call["value"] = False
                    first_read.set()
                    self.assertTrue(release_first.wait(timeout=2))
                else:
                    second_read.set()
                    self.assertTrue(allow_second.wait(timeout=2))
                return payload

            first_done = threading.Event()
            second_done = threading.Event()

            def update_title() -> None:
                try:
                    store_fs.update_capture(capture.id, title="title-from-worker")
                finally:
                    first_done.set()

            def update_error() -> None:
                try:
                    store_fs.update_capture(capture.id, error_message="error-from-retry")
                finally:
                    second_done.set()

            with patch.object(store_fs, "_read_json", side_effect=coordinated_read):
                first_thread = threading.Thread(target=update_title)
                second_thread = threading.Thread(target=update_error)
                first_thread.start()
                self.assertTrue(first_read.wait(timeout=2))
                second_thread.start()

                # With the old split transaction, the second read happens
                # before the first write.  Let it finish first so the test
                # deterministically exercises the lost-update interleaving.
                second_read_before_release = second_read.wait(timeout=0.25)
                if second_read_before_release:
                    allow_second.set()
                    self.assertTrue(second_done.wait(timeout=2))
                release_first.set()
                self.assertTrue(first_done.wait(timeout=2))
                if not second_read_before_release:
                    self.assertTrue(second_read.wait(timeout=2))
                    allow_second.set()
                    self.assertTrue(second_done.wait(timeout=2))
                first_thread.join(timeout=2)
                second_thread.join(timeout=2)

            final = store_fs.get_capture(capture.id)
            self.assertIsNotNone(final)
            self.assertEqual(final.title, "title-from-worker")
            self.assertEqual(final.error_message, "error-from-retry")

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_platform_access_recovery_reason_codes_are_user_action_states(self) -> None:
        self.assertTrue(
            {
                "browser_challenge_required",
                "browser_session_expired",
                "browser_network_risk",
                "douyin_fresh_cookies_required",
            }.issubset(pipeline.USER_ACTION_REQUIRED_REASON_CODES)
        )

    def test_platform_challenge_is_persisted_as_user_action_not_failure(self) -> None:
        capture_url = "https://www.xiaohongshu.com/discovery/item/1234567890abcdef"
        capture = SimpleNamespace(
            id="capture-platform-challenge",
            status="queued",
            result_state="processing",
            input_type="url",
            url=capture_url,
            title=capture_url,
            source_platform="xiaohongshu",
            content_type="image_article",
            started_at=None,
            finished_at=None,
            error_stage=None,
            error_message=None,
            source=SourceMetaModel(
                platform="xiaohongshu",
                content_type="image_article",
                raw_input_text=capture_url,
            ),
            processing=pipeline.ProcessingStateModel(),
        )
        resolved = SimpleNamespace(
            normalized_url=capture_url,
            platform="xiaohongshu",
            content_type="image_article",
            input_warning="",
            cleaned_input=capture_url,
            detected_urls=[capture_url],
            selection_reason="direct_url",
        )
        updates: list[dict] = []

        def update_capture(capture_id: str, **fields):
            self.assertEqual(capture_id, capture.id)
            updates.append(fields)
            for key, value in fields.items():
                setattr(capture, key, value)
            return capture

        adapter = SimpleNamespace(
            extract=lambda *args, **kwargs: (_ for _ in ()).throw(
                ExtractionError(
                    "extract",
                    "当前页面触发了平台验证。",
                    reason_code="browser_challenge_required",
                    retryable=True,
                )
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            pipeline.repository, "get", return_value=capture
        ), patch.object(pipeline.repository, "update", side_effect=update_capture), patch.object(
            pipeline.repository,
            "capture_workspace",
            return_value=Path(tmp_dir),
        ), patch.object(pipeline, "ARTIFACTS_DIR", Path(tmp_dir) / "artifacts"), patch.object(
            pipeline, "resolve_url", return_value=resolved
        ), patch.object(pipeline, "get_url_adapter", return_value=adapter):
            pipeline.active_capture_ids.clear()
            pipeline.queued_capture_ids.clear()
            pipeline.process_capture(capture.id)

        self.assertTrue(updates)
        self.assertEqual(capture.status, "needs_user_action")
        self.assertEqual(capture.result_state, "awaiting_user")
        self.assertEqual(capture.processing.current_stage, "awaiting_user")
        self.assertEqual(capture.processing.failure_reason_code, "browser_challenge_required")
        self.assertTrue(capture.processing.retryable)
        self.assertFalse(any(fields.get("status") == "failed" for fields in updates))

    def test_recovery_of_missing_local_file_stays_actionable(self) -> None:
        capture = SimpleNamespace(
            id="capture-missing-local-file",
            status="queued",
            input_type="file",
            storage_path=str(Path.cwd() / "tests_runtime" / "missing-upload" / "source.mp4"),
            updated_at="2099-01-01T00:00:00+00:00",
            created_at="2099-01-01T00:00:00+00:00",
            processing=pipeline.ProcessingStateModel(
                retry_count=0,
                current_stage="queued",
                progress_percent=2,
                progress_detail="",
                failure_reason_code=None,
                retryable=True,
                trace=[],
                warnings=[],
            ),
        )
        updates = []
        with patch.object(pipeline.repository, "list_pending_ids", return_value=[capture.id]), patch.object(
            pipeline.repository, "get", return_value=capture
        ), patch.object(pipeline.repository, "update", side_effect=lambda _id, **kwargs: updates.append(kwargs)), patch.object(
            pipeline, "write_capture_event"
        ):
            pipeline.recover_pending_captures()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["status"], "needs_user_action")
        self.assertEqual(updates[0]["result_state"], "awaiting_user")
        self.assertEqual(updates[0]["processing"].current_stage, "awaiting_user")
        self.assertEqual(updates[0]["processing"].failure_reason_code, "input_file_missing")

    def test_exhausted_transient_retry_stays_recoverable(self) -> None:
        capture_url = "https://example.com/unstable-source"
        capture = SimpleNamespace(
            id="capture-retry-exhausted",
            status="queued",
            result_state="processing",
            input_type="url",
            url=capture_url,
            title=capture_url,
            source_platform="generic_web",
            content_type="webpage",
            started_at=None,
            finished_at=None,
            error_stage=None,
            error_message=None,
            source=SourceMetaModel(
                platform="generic_web",
                content_type="webpage",
                raw_input_text=capture_url,
            ),
            processing=pipeline.ProcessingStateModel(
                retry_count=pipeline.AUTOMATIC_RETRY_LIMIT,
            ),
        )
        resolved = SimpleNamespace(
            normalized_url=capture_url,
            platform="generic_web",
            content_type="webpage",
            input_warning="",
            cleaned_input=capture_url,
            detected_urls=[capture_url],
            selection_reason="direct_url",
        )
        updates: list[dict] = []

        def update_capture(capture_id: str, **fields):
            self.assertEqual(capture_id, capture.id)
            updates.append(fields)
            for key, value in fields.items():
                setattr(capture, key, value)
            return capture

        adapter = SimpleNamespace(
            extract=lambda *args, **kwargs: (_ for _ in ()).throw(
                ExtractionError(
                    "extract",
                    "无法连接来源站点。",
                    reason_code="source_fetch_failed",
                    retryable=True,
                )
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            pipeline.repository, "get", return_value=capture
        ), patch.object(pipeline.repository, "update", side_effect=update_capture), patch.object(
            pipeline.repository,
            "capture_workspace",
            return_value=Path(tmp_dir),
        ), patch.object(pipeline, "ARTIFACTS_DIR", Path(tmp_dir) / "artifacts"), patch.object(
            pipeline, "resolve_url", return_value=resolved
        ), patch.object(pipeline, "get_url_adapter", return_value=adapter):
            pipeline.active_capture_ids.clear()
            pipeline.queued_capture_ids.clear()
            pipeline.process_capture(capture.id)

        self.assertTrue(updates)
        self.assertEqual(capture.status, "needs_user_action")
        self.assertEqual(capture.result_state, "awaiting_user")
        self.assertEqual(capture.processing.current_stage, "awaiting_user")
        self.assertEqual(capture.processing.failure_reason_code, "retry_exhausted")
        self.assertTrue(capture.processing.retryable)
        self.assertIn("已自动重试", capture.error_message)
        self.assertFalse(any(fields.get("status") == "failed" for fields in updates))

    def test_unexpected_processing_error_is_requeued_before_attention_state(self) -> None:
        capture_url = "https://example.com/unexpected-source"
        capture = SimpleNamespace(
            id="capture-unexpected-error",
            status="queued",
            result_state="processing",
            input_type="url",
            url=capture_url,
            title=capture_url,
            source_platform="generic_web",
            content_type="webpage",
            started_at=None,
            finished_at=None,
            error_stage=None,
            error_message=None,
            source=SourceMetaModel(
                platform="generic_web",
                content_type="webpage",
                raw_input_text=capture_url,
            ),
            processing=pipeline.ProcessingStateModel(),
        )
        resolved = SimpleNamespace(
            normalized_url=capture_url,
            platform="generic_web",
            content_type="webpage",
            input_warning="",
            cleaned_input=capture_url,
            detected_urls=[capture_url],
            selection_reason="direct_url",
        )
        updates: list[dict] = []

        def update_capture(capture_id: str, **fields):
            self.assertEqual(capture_id, capture.id)
            updates.append(fields)
            for key, value in fields.items():
                setattr(capture, key, value)
            return capture

        adapter = SimpleNamespace(
            extract=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("simulated adapter defect")
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            pipeline.repository, "get", return_value=capture
        ), patch.object(pipeline.repository, "update", side_effect=update_capture), patch.object(
            pipeline.repository,
            "capture_workspace",
            return_value=Path(tmp_dir),
        ), patch.object(pipeline, "ARTIFACTS_DIR", Path(tmp_dir) / "artifacts"), patch.object(
            pipeline, "resolve_url", return_value=resolved
        ), patch.object(pipeline, "get_url_adapter", return_value=adapter), patch.object(
            pipeline, "enqueue_capture"
        ) as enqueue_capture, patch.object(pipeline.logger, "exception"):
            pipeline.active_capture_ids.clear()
            pipeline.queued_capture_ids.clear()
            pipeline.process_capture(capture.id)

        self.assertEqual(capture.status, "queued")
        self.assertEqual(capture.result_state, "processing")
        self.assertEqual(capture.processing.current_stage, "queued")
        self.assertEqual(capture.processing.retry_count, 1)
        self.assertIsNone(capture.processing.failure_reason_code)
        self.assertTrue(capture.processing.retryable)
        self.assertIsNone(capture.error_message)
        enqueue_capture.assert_called_once_with(capture.id)

    def test_automatic_retry_only_accepts_bounded_transient_errors(self) -> None:
        capture = SimpleNamespace(
            processing=SimpleNamespace(retry_count=0),
        )
        transient = ExtractionError(
            "extract",
            "网络暂时不可用",
            reason_code="source_fetch_failed",
            retryable=True,
        )
        user_action = ExtractionError(
            "extract",
            "需要刷新会话",
            reason_code="browser_session_expired",
            retryable=True,
        )

        self.assertTrue(pipeline._should_automatically_retry(capture, transient))
        self.assertFalse(pipeline._should_automatically_retry(capture, user_action))

        capture.processing.retry_count = 1
        self.assertTrue(pipeline._should_automatically_retry(capture, transient))

        capture.processing.retry_count = pipeline.AUTOMATIC_RETRY_LIMIT
        self.assertFalse(pipeline._should_automatically_retry(capture, transient))

    def test_image_preview_preserves_original_and_bounds_longest_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "source.jpg"
            output = av.open(str(source), mode="w")
            try:
                stream = output.add_stream("mjpeg", rate=1)
                stream.width = 1200
                stream.height = 800
                stream.pix_fmt = "yuvj420p"
                frame = av.VideoFrame(1200, 800, "yuvj420p")
                for plane in frame.planes:
                    plane.update(bytes([128]) * plane.buffer_size)
                for packet in stream.encode(frame):
                    output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
            finally:
                output.close()

            original_bytes = source.read_bytes()
            width, height, thumbnail = pipeline._prepare_image_preview(source)

            self.assertEqual((width, height), (1200, 800))
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertIsNotNone(thumbnail)
            self.assertTrue(thumbnail.is_file())
            with av.open(str(thumbnail)) as container:
                thumbnail_frame = next(container.decode(video=0))
            self.assertEqual(max(thumbnail_frame.width, thumbnail_frame.height), 640)

    def test_desktop_runtime_does_not_apply_cloud_duration_limit(self) -> None:
        source = SourceMetaModel(platform="bilibili", content_type="video", duration_seconds=4 * 60 * 60)

        with patch.object(pipeline, "RUNTIME_TARGET", "windows_desktop"):
            pipeline._enforce_free_duration_limit(source)

    def test_cloud_demo_still_applies_duration_limit(self) -> None:
        source = SourceMetaModel(platform="bilibili", content_type="video", duration_seconds=31 * 60)

        with patch.object(pipeline, "RUNTIME_TARGET", "cloud_demo"):
            with self.assertRaises(ExtractionError) as raised:
                pipeline._enforce_free_duration_limit(source)

        self.assertEqual(raised.exception.stage, "limit")
        self.assertIn("在线体验", str(raised.exception))
        self.assertNotIn("免费版", str(raised.exception))

    def test_cloud_duration_message_uses_configured_limit(self) -> None:
        source = SourceMetaModel(platform="bilibili", content_type="video", duration_seconds=16 * 60)

        with patch.object(pipeline, "RUNTIME_TARGET", "cloud_demo"), patch.object(
            pipeline, "FREE_DURATION_SECONDS", 15 * 60
        ):
            with self.assertRaises(ExtractionError) as raised:
                pipeline._enforce_free_duration_limit(source)

        self.assertIn("15 分钟", str(raised.exception))
        self.assertNotIn("30 分钟", str(raised.exception))

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

    def test_capture_diagnostics_marks_automatic_retry_as_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            diagnostics = CaptureDiagnostics("cap-retry")
            diagnostics.path = Path(tmp_dir) / "capture_traces" / "cap-retry.jsonl"
            diagnostics.finish_capture(status="queued", retry_count=1)
            records = read_trace_records("cap-retry", logs_dir=Path(tmp_dir))

        self.assertEqual(records[-1]["status"], "queued")
        self.assertEqual(records[-1]["stage"], "queued")

    def test_capture_diagnostics_projects_legacy_failure_to_actionable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            diagnostics = CaptureDiagnostics("cap-legacy-failure-trace")
            diagnostics.path = Path(tmp_dir) / "capture_traces" / "cap-legacy-failure-trace.jsonl"
            diagnostics.finish_capture(status="failed")
            records = read_trace_records("cap-legacy-failure-trace", logs_dir=Path(tmp_dir))

        self.assertEqual(records[-1]["status"], "needs_user_action")
        self.assertEqual(records[-1]["stage"], "awaiting_user")

    def test_capture_diagnostics_redacts_sensitive_url_query_and_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            diagnostics = CaptureDiagnostics("cap-redaction")
            diagnostics.path = Path(tmp_dir) / "capture_traces" / "cap-redaction.jsonl"
            diagnostics.event(
                "external_error",
                detail="GET https://example.com/article?sig=secret-value&lang=zh",
                access_token="do-not-write",
            )
            records = read_trace_records("cap-redaction", logs_dir=Path(tmp_dir))

        record = records[-1]
        self.assertEqual(record["access_token"], "[redacted]")
        self.assertIn("sig=[redacted]", record["detail"])
        self.assertIn("lang=zh", record["detail"])
        self.assertNotIn("secret-value", record["detail"])

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

    def test_local_file_title_hides_randomized_storage_name(self) -> None:
        resolved = pipeline._result_title(
            capture=SimpleNamespace(title="访谈录音.wav"),
            source=SourceMetaModel(platform="local_file", content_type="audio"),
            extraction=ExtractionOutcome(
                platform="local_file",
                content_type="audio",
                title="5f4dcc3b5aa765d61d8327deb882cf99_访谈录音.wav",
            ),
        )

        self.assertEqual(resolved, "访谈录音.wav")

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

    def test_isolated_worker_failure_requeues_pending_capture(self) -> None:
        capture = SimpleNamespace(
            id="cap-worker-failure",
            status="processing",
            processing=pipeline.ProcessingStateModel(
                current_stage="extract",
                progress_percent=30,
            ),
        )
        updated = {}

        with patch.object(pipeline.repository, "get", return_value=capture), patch.object(
            pipeline.repository,
            "update",
            side_effect=lambda capture_id, **fields: updated.update(
                {"capture_id": capture_id, **fields}
            ),
        ), patch.object(pipeline, "enqueue_capture") as enqueue_capture:
            pipeline._persist_isolated_worker_failure(
                capture.id,
                RuntimeError("simulated worker crash"),
            )

        self.assertEqual(updated["capture_id"], capture.id)
        self.assertEqual(updated["status"], "queued")
        self.assertIsNone(updated["error_stage"])
        self.assertEqual(updated["processing"].current_stage, "queued")
        self.assertEqual(updated["processing"].retry_count, 1)
        self.assertIsNone(updated["processing"].failure_reason_code)
        self.assertTrue(updated["processing"].retryable)
        self.assertIsNone(updated["error_message"])
        enqueue_capture.assert_called_once_with(capture.id)

    def test_repeated_isolated_worker_failure_stays_actionable(self) -> None:
        capture = SimpleNamespace(
            id="cap-worker-failure-exhausted",
            status="processing",
            processing=pipeline.ProcessingStateModel(
                current_stage="extract",
                progress_percent=30,
                retry_count=pipeline.AUTOMATIC_RETRY_LIMIT,
            ),
        )
        updated = {}

        with patch.object(pipeline.repository, "get", return_value=capture), patch.object(
            pipeline.repository,
            "update",
            side_effect=lambda capture_id, **fields: updated.update(
                {"capture_id": capture_id, **fields}
            ),
        ):
            pipeline._persist_isolated_worker_failure(
                capture.id,
                RuntimeError("simulated repeated worker crash"),
            )

        self.assertEqual(updated["capture_id"], capture.id)
        self.assertEqual(updated["status"], "needs_user_action")
        self.assertEqual(updated["result_state"], "awaiting_user")
        self.assertEqual(updated["processing"].current_stage, "awaiting_user")
        self.assertEqual(updated["processing"].failure_reason_code, "internal_error")
        self.assertTrue(updated["processing"].retryable)
        self.assertNotIn("simulated repeated worker crash", updated["error_message"])

    def test_legacy_failed_worker_record_is_repaired_to_actionable_state(self) -> None:
        capture = SimpleNamespace(
            id="cap-worker-legacy-failed",
            status="failed",
            error_stage="extract",
            processing=pipeline.ProcessingStateModel(
                current_stage="failed",
                progress_percent=0,
            ),
        )
        updated = {}

        with patch.object(pipeline.repository, "get", return_value=capture), patch.object(
            pipeline.repository,
            "update",
            side_effect=lambda capture_id, **fields: updated.update(
                {"capture_id": capture_id, **fields}
            ),
        ):
            pipeline._persist_isolated_worker_failure(
                capture.id,
                RuntimeError("simulated legacy worker crash"),
            )

        self.assertEqual(updated["capture_id"], capture.id)
        self.assertEqual(updated["status"], "needs_user_action")
        self.assertEqual(updated["result_state"], "awaiting_user")
        self.assertEqual(updated["error_stage"], "extract")
        self.assertEqual(updated["processing"].current_stage, "awaiting_user")
        self.assertEqual(updated["processing"].failure_reason_code, "internal_error")
        self.assertTrue(updated["processing"].retryable)
        self.assertNotIn("simulated legacy worker crash", updated["error_message"])

    def test_store_reads_legacy_failed_json_as_actionable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            capture_dir = root / "legacy-read"
            capture_dir.mkdir(parents=True)
            with patch.object(store_fs, "CAPTURES_DIR", root), patch.object(
                store_fs, "ARTIFACTS_DIR", root / "artifacts"
            ):
                payload = store_fs.create_capture(
                    input_type="url",
                    source_platform="generic_web",
                    content_type="webpage",
                    url="https://example.com/legacy-read",
                ).model_dump(mode="json")
                store_fs.delete_capture(payload["id"])
                payload["id"] = "legacy-read"
                payload["status"] = "failed"
                payload["result_state"] = "failed"
                payload["processing"]["current_stage"] = "failed"
                payload["processing"]["progress_percent"] = 0
                (capture_dir / "capture.json").write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                loaded = store_fs.get_capture("legacy-read")
                listed = store_fs.list_captures(limit=10)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, "needs_user_action")
            self.assertEqual(loaded.result_state, "awaiting_user")
            self.assertEqual(loaded.processing.current_stage, "awaiting_user")
            self.assertEqual(loaded.processing.progress_percent, 18)
            self.assertEqual([item.status for item in listed], ["needs_user_action"])

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

            with (
                patch.object(store_fs.os, "replace", side_effect=flaky_replace),
                patch.object(store_fs, "_write_recovery_snapshot"),
            ):
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
            self.assertTrue(store_fs._recovery_snapshot_path(target).is_file())

    def test_read_json_returns_last_good_snapshot_during_transient_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "capture.json"
            target.write_text('{"id":"cap-snapshot","status":"processing"}', encoding="utf-8")
            self.assertEqual(store_fs._read_json(target)["id"], "cap-snapshot")

            with (
                patch.object(Path, "read_text", side_effect=PermissionError("busy")),
                patch.object(store_fs, "_STORE_READ_ATTEMPTS", 2),
                patch.object(store_fs, "_store_retry_delay", return_value=0),
            ):
                payload = store_fs._read_json(target)

            self.assertEqual(payload, {"id": "cap-snapshot", "status": "processing"})

    def test_read_json_uses_disk_snapshot_after_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "capture.json"
            store_fs._write_json(target, {"id": "cap-disk-snapshot", "status": "processing"})
            with store_fs._LAST_GOOD_JSON_GUARD:
                store_fs._LAST_GOOD_JSON.clear()

            real_read_text = Path.read_text

            def locked_primary(path: Path, *args, **kwargs):
                if path == target:
                    raise PermissionError("busy")
                return real_read_text(path, *args, **kwargs)

            with (
                patch.object(Path, "read_text", autospec=True, side_effect=locked_primary),
                patch.object(store_fs, "_STORE_READ_ATTEMPTS", 2),
                patch.object(store_fs, "_store_retry_delay", return_value=0),
            ):
                payload = store_fs._read_json(target)

            self.assertEqual(payload, {"id": "cap-disk-snapshot", "status": "processing"})

    def test_store_retry_budget_is_longer_than_the_old_short_lock_window(self) -> None:
        self.assertGreaterEqual(store_fs._STORE_READ_ATTEMPTS, 32)
        self.assertGreaterEqual(store_fs._STORE_WRITE_ATTEMPTS, 32)
        self.assertLessEqual(
            store_fs._store_retry_delay(store_fs._STORE_READ_ATTEMPTS - 1),
            store_fs._STORE_RETRY_DELAY_CAP_SECONDS,
        )

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
        ), patch.object(
            pipeline, "FFMPEG_PATH", media_path
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
        self.assertEqual(Path(source_audio.path), artifacts_dir / capture.id / "source_audio.m4a")
        self.assertEqual(source_audio.mime_type, "audio/mp4")
        self.assertEqual(Path(source_audio.path).read_bytes(), b"audio")

    def test_persist_audio_result_saves_remote_cover_locally(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        capture = SimpleNamespace(
            id="cap-audio-cover",
            title="Podcast",
            url="https://www.xiaoyuzhoufm.com/episode/demo",
            source=SourceMetaModel(
                platform="xiaoyuzhou",
                content_type="audio",
                canonical_url="https://www.xiaoyuzhoufm.com/episode/demo",
                thumbnail_url="https://example.com/cover.jpg",
            ),
        )

        def download(_: str, target: Path, **__: object) -> Path:
            target.write_bytes(b"cover")
            return target

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_download_image", side_effect=download
        ):
            persisted = pipeline._persist_result_artifacts(
                capture,
                ResultDocumentModel(primary_text="body", primary_result_type="transcript"),
            )

        cover = next(item for item in persisted.artifacts if item.type == "cover_image")
        self.assertEqual(cover.mime_type, "image/jpeg")
        self.assertEqual(Path(cover.path).read_bytes(), b"cover")

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        if root.exists():
            root.rmdir()

    def test_ensure_capture_cover_backfills_legacy_audio_result(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        capture = SimpleNamespace(
            id="legacy-podcast-cover",
            title="Podcast",
            url="https://www.xiaoyuzhoufm.com/episode/demo",
            source=SourceMetaModel(
                platform="xiaoyuzhou",
                content_type="audio",
                canonical_url="https://www.xiaoyuzhoufm.com/episode/demo",
                thumbnail_url="https://example.com/cover.jpg",
            ),
            result=ResultDocumentModel(primary_text="body"),
        )

        def download(_: str, target: Path, **__: object) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"cover")
            return target

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_download_image", side_effect=download
        ):
            result = pipeline.ensure_capture_cover(capture)

        cover = next(item for item in result.artifacts if item.type == "cover_image")
        self.assertEqual(cover.status, "ready")
        self.assertEqual(Path(cover.path).read_bytes(), b"cover")

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        if root.exists():
            root.rmdir()

    def test_repair_legacy_m4a_artifact_with_mp3_name(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        root.mkdir(parents=True, exist_ok=True)
        legacy_path = root / "source_audio.mp3"
        legacy_path.write_bytes(b"m4a-container")
        capture = SimpleNamespace(
            id="legacy-audio",
            result=ResultDocumentModel(
                primary_text="body",
                artifacts=[
                    ArtifactModel(
                        type="source_audio",
                        label="原音频",
                        path=str(legacy_path),
                        download_url="/v1/captures/legacy-audio/artifacts/source_audio",
                        mime_type="audio/mpeg",
                    )
                ],
            ),
        )

        with patch.object(pipeline, "_audio_codec_for_path", return_value="aac"), patch.object(
            pipeline, "_audio_delivery_extension", return_value=".m4a"
        ):
            changed = pipeline.repair_legacy_audio_artifact(capture)

        artifact = capture.result.artifacts[0]
        self.assertTrue(changed)
        self.assertEqual(Path(artifact.path), root / "source_audio.m4a")
        self.assertEqual(artifact.mime_type, "audio/mp4")
        self.assertTrue(Path(artifact.path).is_file())
        self.assertFalse(legacy_path.exists())
        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        if root.exists():
            root.rmdir()

    def test_failed_image_download_keeps_indexed_failed_artifact(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        capture = SimpleNamespace(
            id="cap-image-failure",
            title="Image note",
            url="https://example.com/note",
            source=SourceMetaModel(
                platform="xiaohongshu",
                content_type="image_article",
                canonical_url="https://example.com/note",
                image_urls=["https://example.com/one.jpg", "https://example.com/two.jpg"],
                live_photo_video_urls=[],
            ),
        )

        def download(url: str, target: Path, **_: object) -> Path:
            if url.endswith("two.jpg"):
                raise pipeline.ExtractionError("download", "资源下载失败")
            target.write_bytes(b"image")
            return target

        with patch.object(pipeline, "ARTIFACTS_DIR", artifacts_dir), patch.object(
            pipeline, "_download_image", side_effect=download
        ):
            persisted = pipeline._persist_result_artifacts(
                capture,
                ResultDocumentModel(primary_text="body", primary_result_type="article"),
            )

        by_type = {item.type: item for item in persisted.artifacts}
        self.assertEqual(by_type["image_01"].status, "ready")
        self.assertEqual(by_type["image_02"].status, "failed")
        self.assertTrue(by_type["image_02"].optional)
        self.assertNotIn("images_zip", by_type)

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        if root.exists():
            root.rmdir()

    def test_retry_failed_image_replaces_artifact_and_rebuilds_zip(self) -> None:
        root = Path.cwd() / "tests_runtime" / "artifact_runs" / uuid4().hex
        artifact_dir = root / "artifacts" / "cap-image-retry"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        first_path = artifact_dir / "image_01.jpg"
        first_path.write_bytes(b"first")
        capture = SimpleNamespace(
            id="cap-image-retry",
            title="Retry note",
            url="https://example.com/note",
            source=SourceMetaModel(
                platform="xiaohongshu",
                content_type="image_article",
                canonical_url="https://example.com/note",
                image_urls=["https://example.com/one.jpg", "https://example.com/two.jpg"],
                live_photo_video_urls=[],
            ),
            result=ResultDocumentModel(
                primary_text="body",
                artifacts=[
                    pipeline._artifact_entry("cap-image-retry", "image_01", "图片 1", first_path),
                    pipeline._artifact_entry(
                        "cap-image-retry",
                        "image_02",
                        "图片 2",
                        artifact_dir / "image_02.jpg",
                        status="failed",
                        optional=True,
                    ),
                ],
            ),
        )

        def download(_: str, target: Path, **__: object) -> Path:
            target.write_bytes(b"second")
            return target

        with patch.object(pipeline, "ARTIFACTS_DIR", root / "artifacts"), patch.object(
            pipeline, "_download_image", side_effect=download
        ):
            result = pipeline.retry_capture_image(capture, 2)

        by_type = {item.type: item for item in result.artifacts}
        self.assertEqual(by_type["image_02"].status, "ready")
        self.assertEqual(Path(by_type["image_02"].path).read_bytes(), b"second")
        self.assertEqual(by_type["images_zip"].status, "ready")
        with zipfile.ZipFile(by_type["images_zip"].path) as archive:
            self.assertEqual(sorted(archive.namelist()), ["image_01.jpg", "image_02.jpg"])

        for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        if root.exists():
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

        self.assertEqual(result.result_notice, "文本已整理完成，原视频文件暂未就绪。")

    def test_media_capture_keeps_text_when_delivery_artifact_is_missing(self) -> None:
        capture = SimpleNamespace(
            source=SourceMetaModel(platform="youtube", content_type="video"),
        )
        result = ResultDocumentModel(primary_text="字幕已经拿到")

        self.assertFalse(pipeline._ensure_required_media_artifact(capture, result))

    def test_media_capture_still_protects_empty_result(self) -> None:
        capture = SimpleNamespace(
            source=SourceMetaModel(platform="youtube", content_type="video"),
        )
        result = ResultDocumentModel()

        with self.assertRaises(ExtractionError) as context:
            pipeline._ensure_required_media_artifact(capture, result)

        self.assertEqual(context.exception.reason_code, "download_failed")
        self.assertTrue(context.exception.retryable)
        self.assertTrue(any("source_media" in warning for warning in context.exception.warnings))

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

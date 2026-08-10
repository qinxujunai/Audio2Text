from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as capture_main
import app.pipeline as capture_pipeline
import app.store_fs as store_fs
from app.schemas import ArtifactModel, ResultDocumentModel, SourceMetaModel


class CaptureApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "tests_runtime" / "api_runs" / uuid4().hex
        self.captures_dir = self.root / "captures"
        self.artifacts_dir = self.root / "artifacts"
        self.temp_upload_dir = self.root / "temp"

        for folder in [self.captures_dir, self.artifacts_dir, self.temp_upload_dir]:
            folder.mkdir(parents=True, exist_ok=True)

        self.patchers = [
            patch.object(store_fs, "CAPTURES_DIR", self.captures_dir),
            patch.object(store_fs, "ARTIFACTS_DIR", self.artifacts_dir),
            patch.object(store_fs, "CAPTURE_HISTORY_LIMIT", 10),
            patch.object(capture_main, "TEMP_DIR", self.temp_upload_dir),
            patch.object(capture_main, "CAPTURE_HISTORY_LIMIT", 10),
            patch.object(capture_main, "MAX_UPLOAD_SIZE_MB", 2),
            patch.object(capture_main.repository, "init", lambda: None),
            patch.object(capture_main, "recover_pending_captures", lambda: None),
            patch.object(capture_main, "ensure_worker_started", lambda: None),
            patch.object(capture_pipeline, "ensure_worker_started", lambda: None),
        ]
        for patcher in self.patchers:
            patcher.start()

        self.client = TestClient(capture_main.app)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        if self.root.exists():
            for child in sorted(self.root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            self.root.rmdir()

    def test_desktop_runtime_requires_loopback_token_and_known_origin(self) -> None:
        with (
            patch.object(capture_main, "RUNTIME_TARGET", "windows_desktop"),
            patch.object(capture_main, "DESKTOP_TOKEN", "desktop-test-token"),
            patch.object(
                capture_main,
                "DESKTOP_ALLOWED_ORIGINS",
                capture_main.DESKTOP_ALLOWED_ORIGINS | {"http://localhost:5173"},
            ),
        ):
            unauthorized = self.client.get("/health")
            wrong_origin = self.client.get(
                "/health",
                headers={
                    "Origin": "https://example.com",
                    "X-Wanxiang-Desktop-Token": "desktop-test-token",
                },
            )
            authorized = self.client.get(
                "/health",
                headers={
                    "Origin": "http://tauri.localhost",
                    "X-Wanxiang-Desktop-Token": "desktop-test-token",
                },
            )
            event_source_style = self.client.get(
                "/health?desktop_token=desktop-test-token",
                headers={"Origin": "http://tauri.localhost"},
            )
            preflight = self.client.options(
                "/config",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "x-wanxiang-desktop-token",
                },
            )
            hostile_preflight = self.client.options(
                "/config",
                headers={
                    "Origin": "https://example.com",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "x-wanxiang-desktop-token",
                },
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(event_source_style.status_code, 200)
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(
            preflight.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )
        self.assertEqual(hostile_preflight.status_code, 403)

    def test_runtime_pack_api_is_desktop_only_and_sanitized(self) -> None:
        web_response = self.client.get("/v1/runtime/packs")
        with (
            patch.object(capture_main, "RUNTIME_TARGET", "windows_desktop"),
            patch.object(capture_main, "DESKTOP_TOKEN", "desktop-test-token"),
            patch.object(
                capture_main.runtime_pack_manager,
                "status",
                return_value=[{"id": "asr", "version": "1", "installed": False}],
            ),
        ):
            desktop_response = self.client.get(
                "/v1/runtime/packs",
                headers={"X-Wanxiang-Desktop-Token": "desktop-test-token"},
            )

        self.assertEqual(web_response.status_code, 404)
        self.assertEqual(desktop_response.status_code, 200)
        self.assertEqual(desktop_response.json()["packs"][0]["id"], "asr")

    def test_config_omits_runtime_details(self) -> None:
        response = self.client.get("/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("runtime", payload)
        self.assertIn("max_upload_size_mb", payload)
        self.assertIn("free_duration_minutes", payload)
        self.assertIn("deployment_mode", payload)
        self.assertIn("public_preview_mode", payload)
        self.assertIn("transcription_available", payload)
        self.assertIn("transcription_provider", payload)
        self.assertIn("runtime_target", payload)
        self.assertIn("capabilities", payload)
        self.assertNotIn("commit", payload)

    def test_health_exposes_consistent_sanitized_runtime_identity(self) -> None:
        with patch.object(capture_main, "APP_VERSION", "2.0.0-test"), patch.object(
            capture_main, "APP_COMMIT", "abc1234"
        ), patch.object(capture_main, "DEPLOYMENT_MODE", "cloud_preview"), patch.object(
            capture_main, "RUNTIME_TARGET", "cloud_demo"
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["deployment_mode"], "cloud_preview")
        self.assertEqual(payload["runtime_target"], "cloud_demo")
        self.assertEqual(payload["version"], "2.0.0-test")
        self.assertEqual(payload["commit"], "abc1234")
        self.assertNotIn("run_mode", payload)
        self.assertIsInstance(payload["components"], dict)

    def test_windows_connection_reset_filter_is_narrow(self) -> None:
        with patch.object(capture_main.sys, "platform", "win32"):
            self.assertTrue(
                capture_main._is_windows_connection_reset_noise(
                    {
                        "exception": ConnectionResetError(10054, "remote host closed the connection"),
                        "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost()>",
                    }
                )
            )
            self.assertFalse(
                capture_main._is_windows_connection_reset_noise(
                    {
                        "exception": RuntimeError("boom"),
                        "handle": "<Handle user_callback()>",
                    }
                )
            )

    def test_capture_envelope_exposes_canonical_url_but_omits_internal_fields(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="wechat_article",
            content_type="article",
            url="https://example.com/post",
        )
        source = SourceMetaModel(
            platform="wechat_article",
            content_type="article",
            canonical_url="https://example.com/post",
            source_item_id="internal-id",
            author="Author",
            description="desc",
        )
        result = ResultDocumentModel(
            primary_text="hello world",
            primary_result_type="article",
            artifacts=[
                ArtifactModel(
                    type="txt",
                    label="文字结果",
                    path=str(self.root / "secret.txt"),
                    download_url=f"/v1/captures/{capture.id}/artifacts/txt",
                    mime_type="text/plain",
                    size_bytes=12,
                )
            ],
        )
        capture_main.repository.update(
            capture.id,
            status="done",
            source=source,
            result=result,
        )

        response = self.client.get(f"/v1/captures/{capture.id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["source"]["canonical_url"], "https://example.com/post")
        self.assertNotIn("source_item_id", payload["source"])
        self.assertNotIn("source_item_id", payload["result"])
        self.assertNotIn("path", payload["artifacts"][0])
        self.assertIn("progress_percent", payload["capture"])
        self.assertIn("text_source", payload["quality"])
        self.assertEqual(payload["artifacts"][0]["download_url"], f"/v1/captures/{capture.id}/artifacts/txt")
        self.assertEqual(payload["capture"]["result_state"], "complete")
        self.assertEqual(payload["artifacts"][0]["status"], "ready")
        self.assertFalse(payload["artifacts"][0]["optional"])

    def test_capture_envelope_exposes_source_images_and_asset_pending_flag(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="xiaohongshu",
            content_type="image_article",
            url="https://example.com/note",
        )
        source = SourceMetaModel(
            platform="xiaohongshu",
            content_type="image_article",
            canonical_url="https://example.com/note",
            image_urls=["https://example.com/image-1.jpg", "https://example.com/image-2.jpg"],
            live_photo_video_urls=["https://example.com/live-1.mp4"],
        )
        result = ResultDocumentModel(primary_text="图文正文", primary_result_type="article")
        capture_main.repository.update(
            capture.id,
            status="done",
            asset_preparation_pending=True,
            source=source,
            result=result,
        )

        payload = self.client.get(f"/v1/captures/{capture.id}").json()

        self.assertTrue(payload["capture"]["asset_preparation_pending"])
        self.assertEqual(payload["capture"]["result_state"], "text_ready")
        self.assertEqual(len(payload["source"]["images"]), 2)
        self.assertEqual(payload["source"]["images"][0]["index"], 1)
        self.assertEqual(payload["source"]["images"][0]["image_url"], "https://example.com/image-1.jpg")
        self.assertEqual(payload["source"]["images"][0]["live_photo_video_url"], "https://example.com/live-1.mp4")
        self.assertIsNone(payload["source"]["images"][1]["live_photo_video_url"])

    def test_event_names_expose_text_artifact_and_runtime_transitions(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="bilibili",
            content_type="video",
            url="https://www.bilibili.com/video/BV1xx",
        )
        result = ResultDocumentModel(
            primary_text="正文已经可以阅读",
            primary_result_type="transcript",
            artifacts=[
                ArtifactModel(
                    type="source_audio",
                    label="原音频",
                    path=str(self.root / "audio.m4a"),
                    download_url=f"/v1/captures/{capture.id}/artifacts/source_audio",
                    status="ready",
                )
            ],
        )
        capture = capture_main.repository.update(
            capture.id,
            status="done",
            result_state="text_ready",
            asset_preparation_pending=True,
            result=result,
        )
        envelope = capture_main._to_capture_envelope(capture)

        events, artifact_types = capture_main._transition_event_names(
            capture,
            envelope,
            previous_result_state="processing",
            ready_artifact_types=set(),
        )

        self.assertEqual(events, ["text_ready", "artifact_ready"])
        self.assertEqual(artifact_types, {"source_audio"})

        capture.processing.failure_reason_code = "runtime_model_required"
        capture.status = "failed"
        failed_envelope = capture_main._to_capture_envelope(capture)
        events, _ = capture_main._transition_event_names(
            capture,
            failed_envelope,
            previous_result_state="text_ready",
            ready_artifact_types=artifact_types,
        )
        self.assertEqual(events, ["runtime_required"])

    def test_file_upload_uses_capture_workspace_storage(self) -> None:
        response = self.client.post(
            "/v1/captures",
            files={"file": ("voice.mp3", b"1234567890", "audio/mpeg")},
        )

        self.assertEqual(response.status_code, 200)
        capture_id = response.json()["capture_id"]
        capture = capture_main.repository.get(capture_id)
        self.assertIsNotNone(capture)
        self.assertIsNotNone(capture.storage_path)
        storage_path = Path(capture.storage_path)
        self.assertTrue(storage_path.exists())
        self.assertIn(capture_id, str(storage_path))
        self.assertEqual(list(self.temp_upload_dir.iterdir()), [])

    def test_file_upload_rejects_oversized_payload_and_cleans_temp(self) -> None:
        with patch.object(capture_main, "MAX_UPLOAD_SIZE_MB", 1):
            response = self.client.post(
                "/v1/captures",
                files={"file": ("voice.mp3", b"a" * (1024 * 1024 + 1), "audio/mpeg")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("文件过大", response.json()["detail"])
        self.assertEqual(list(self.temp_upload_dir.iterdir()), [])

    def test_retry_rejects_queued_capture(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://youtu.be/test",
        )

        response = self.client.post(f"/v1/captures/{capture.id}/retry")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Capture is already queued.")

    def test_download_source_media_artifact(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://youtu.be/demo",
        )
        media_path = self.artifacts_dir / "demo.mp4"
        media_path.write_bytes(b"video-data")
        source = SourceMetaModel(
            platform="youtube",
            content_type="video",
            canonical_url="https://youtu.be/demo",
        )
        result = ResultDocumentModel(
            primary_text="hello world",
            primary_result_type="subtitle",
            artifacts=[
                ArtifactModel(
                    type="source_media",
                    label="原视频",
                    path=str(media_path),
                    download_url=f"/v1/captures/{capture.id}/artifacts/source_media",
                    mime_type="video/mp4",
                    size_bytes=media_path.stat().st_size,
                )
            ],
        )
        capture_main.repository.update(
            capture.id,
            status="done",
            source=source,
            result=result,
        )

        response = self.client.get(f"/v1/captures/{capture.id}/artifacts/source_media")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"video-data")
        self.assertIn("video/mp4", response.headers.get("content-type", ""))

    def test_create_capture_response_omits_resolution_details(self) -> None:
        resolved = SimpleNamespace(
            normalized_url="https://example.com/post",
            platform="wechat_article",
            content_type="article",
            detected_urls=["https://example.com/post"],
            selection_reason="selected",
            input_warning=None,
            cleaned_input="https://example.com/post",
        )

        with patch.object(capture_main, "resolve_url", return_value=resolved), patch.object(
            capture_main, "infer_source_item_id", return_value=None
        ), patch.object(capture_main, "enqueue_capture", lambda capture_id: None):
            response = self.client.post("/v1/captures", json={"text": "https://example.com/post"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("resolved_url", payload)
        self.assertNotIn("detected_urls", payload)

    def test_xiaoyuzhou_notes_result_is_not_reused_from_cache(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="xiaoyuzhou",
            content_type="audio",
            url="https://www.xiaoyuzhoufm.com/episode/demo",
        )
        capture_main.repository.update(
            capture.id,
            status="done",
            source=SourceMetaModel(
                platform="xiaoyuzhou",
                content_type="audio",
                canonical_url="https://www.xiaoyuzhoufm.com/episode/demo",
                source_item_id="demo",
                description="节目简介",
            ),
            result=ResultDocumentModel(
                primary_text="页面摘要",
                primary_result_type="notes",
                text_source="notes",
                extraction_path=["page_notes"],
            ),
        )

        self.assertFalse(capture_main._capture_is_reusable(capture_main.repository.get(capture.id)))

    def test_xiaoyuzhou_transcript_result_stays_reusable(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="xiaoyuzhou",
            content_type="audio",
            url="https://www.xiaoyuzhoufm.com/episode/demo",
        )
        capture_main.repository.update(
            capture.id,
            status="done",
            source=SourceMetaModel(
                platform="xiaoyuzhou",
                content_type="audio",
                canonical_url="https://www.xiaoyuzhoufm.com/episode/demo",
                source_item_id="demo",
            ),
            result=ResultDocumentModel(
                primary_text="真实口播转写",
                primary_result_type="transcript",
                text_source="transcript",
                transcript_text="真实口播转写",
                extraction_path=["episode_audio", "media_download"],
            ),
        )

        self.assertTrue(capture_main._capture_is_reusable(capture_main.repository.get(capture.id)))

    def test_create_capture_returns_public_message_when_unexpected_error_occurs(self) -> None:
        self.client.close()
        error_client = TestClient(capture_main.app, raise_server_exceptions=False)
        try:
            with patch.object(capture_main.repository, "create", side_effect=RuntimeError("boom")):
                response = error_client.post("/v1/captures", json={"text": "https://www.douyin.com/video/7624387162426887464"})
        finally:
            error_client.close()
            self.client = TestClient(capture_main.app)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "服务暂时繁忙，请稍后重试。")

    def test_get_capture_returns_retryable_busy_message_when_store_is_locked(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://youtu.be/test-busy",
        )

        with patch.object(capture_main.repository, "get", side_effect=store_fs.CaptureStoreUnavailableError("busy")):
            response = self.client.get(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "服务暂时繁忙，请稍后重试。")

    def test_capture_events_returns_retryable_busy_message_when_store_is_locked(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://youtu.be/test-busy-events",
        )

        with patch.object(capture_main.repository, "get", side_effect=store_fs.CaptureStoreUnavailableError("busy")):
            response = self.client.get(f"/v1/captures/{capture.id}/events")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "服务暂时繁忙，请稍后重试。")

    def test_create_capture_rejects_invalid_json_with_400(self) -> None:
        response = self.client.post(
            "/v1/captures",
            content="{bad json",
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "请求内容格式不正确，请检查输入后再试。")

    def test_xiaoyuzhou_stale_notes_result_is_hidden_in_public_payload(self) -> None:
        capture = SimpleNamespace(
            id="cap-xiaoyuzhou-stale",
            input_type="url",
            status="done",
            title="Sample episode",
            source_platform="xiaoyuzhou",
            content_type="audio",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            started_at=None,
            finished_at=None,
            error_stage=None,
            error_message=None,
            source=SourceMetaModel(
                platform="xiaoyuzhou",
                content_type="audio",
                canonical_url="https://www.xiaoyuzhoufm.com/episode/demo",
                description="节目简介",
            ),
            processing=SimpleNamespace(
                current_stage="completed",
                progress_percent=100,
                progress_detail="done",
                retry_count=0,
                retryable=False,
            ),
            result=ResultDocumentModel(
                primary_text="页面摘要",
                primary_result_type="notes",
                text_source="notes",
                notes_text="页面摘要",
                views=capture_main.ResultViewsModel(primary="页面摘要", notes="页面摘要"),
                artifacts=[
                    ArtifactModel(
                        type="txt",
                        label="文本结果",
                        path=str(self.root / "capture.txt"),
                        download_url="/v1/captures/demo/artifacts/txt",
                    )
                ],
            ),
        )

        envelope = capture_main._to_capture_envelope(capture)
        public_item = capture_main._public_capture(capture)

        self.assertEqual(envelope.quality.text_source, "none")
        self.assertEqual(envelope.result.primary_text, "")
        self.assertEqual(envelope.source.description, "")
        self.assertEqual(envelope.artifacts, [])
        self.assertEqual(public_item.preview_text, "")

    def test_delete_completed_capture_removes_record_and_artifacts(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="wechat_article",
            content_type="article",
            url="https://example.com/post",
        )
        capture_dir = capture_main.repository.capture_workspace(capture.id)
        artifact_dir = self.artifacts_dir / capture.id
        capture_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "capture.txt").write_text("hello", encoding="utf-8")
        capture_main.repository.update(
            capture.id,
            status="done",
            source=SourceMetaModel(platform="wechat_article", content_type="article", canonical_url="https://example.com/post"),
            result=ResultDocumentModel(primary_text="hello world", primary_result_type="article"),
        )

        response = self.client.delete(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": True, "capture_id": capture.id})
        self.assertIsNone(capture_main.repository.get(capture.id))
        self.assertFalse(capture_dir.exists())
        self.assertFalse(artifact_dir.exists())

    def test_delete_capture_allows_queued_capture(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://youtu.be/test",
        )
        capture_dir = capture_main.repository.capture_workspace(capture.id)
        artifact_dir = self.artifacts_dir / capture.id
        capture_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        response = self.client.delete(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": True, "capture_id": capture.id})
        self.assertIsNone(capture_main.repository.get(capture.id))
        self.assertFalse(capture_dir.exists())
        self.assertFalse(artifact_dir.exists())

    def test_get_capture_requeues_queued_capture(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://youtu.be/test",
        )

        with patch.object(capture_main, "enqueue_capture") as enqueue_mock:
            response = self.client.get(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        enqueue_mock.assert_called_once_with(capture.id)

    def test_delete_capture_returns_not_found_for_missing_id(self) -> None:
        response = self.client.delete("/v1/captures/missing-capture")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "这条记录已经不存在了。")

    def test_delete_capture_returns_chinese_message_for_processing_capture(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://youtu.be/test-processing-delete",
        )
        capture_main.repository.update(
            capture.id,
            status="processing",
            source=SourceMetaModel(
                platform="youtube",
                content_type="video",
                canonical_url="https://youtu.be/test-processing-delete",
            ),
        )

        response = self.client.delete(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "这条记录还在处理中，暂时不能删除。")

    def test_public_capture_preview_drops_duplicated_title_prefix(self) -> None:
        capture = SimpleNamespace(
            id="cap-preview",
            status="done",
            title="Sample title",
            source_platform="wechat_article",
            content_type="article",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            source=SourceMetaModel(platform="wechat_article", content_type="article", description="Sample title Body preview"),
            result=ResultDocumentModel(primary_text="Sample title\n\nBody preview"),
        )

        item = capture_main._public_capture(capture)

        self.assertEqual(item.title, "Sample title")
        self.assertEqual(item.preview_text, "Body preview")

    def test_public_capture_preview_drops_truncated_title_prefix(self) -> None:
        capture = SimpleNamespace(
            id="cap-preview-ellipsis",
            status="done",
            title="第一句已经足够作为标题…",
            source_platform="douyin",
            content_type="video",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            source=SourceMetaModel(platform="douyin", content_type="video", description=""),
            result=ResultDocumentModel(primary_text="第一句已经足够作为标题。这里才是摘要"),
        )

        item = capture_main._public_capture(capture)

        self.assertEqual(item.preview_text, "这里才是摘要")


if __name__ == "__main__":
    unittest.main()

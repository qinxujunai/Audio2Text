from __future__ import annotations

import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as capture_main
import app.pipeline as capture_pipeline
import app.store_fs as store_fs
from app.schemas import ArtifactModel, ResultDocumentModel, ResultSegmentModel, SourceMetaModel


class CaptureApiTestCase(unittest.TestCase):
    def test_retired_youtube_retry_does_not_queue_or_change_saved_result(self):
        capture = capture_main.repository.create(input_type="url", source_platform="youtube",
                                                 content_type="video", url="https://youtu.be/public")
        capture_main.repository.update(capture.id, status="done",
                                       result=ResultDocumentModel(primary_text="saved original"))
        saved = (self.captures_dir / capture.id / "capture.json").read_bytes()
        with patch.object(capture_main, "_queue_if_needed") as queued:
            response = self.client.post(f"/v1/captures/{capture.id}/retry")
            self.assertEqual(response.status_code, 400)
            self.assertIn("不再支持 YouTube", response.json()["detail"])
            queued.assert_not_called()
        self.assertEqual((self.captures_dir / capture.id / "capture.json").read_bytes(), saved)
        self.assertEqual(self.client.get(f"/v1/captures/{capture.id}").status_code, 200)

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

    def test_platform_verification_waiting_state_is_not_reported_as_failed(self) -> None:
        capture = store_fs.create_capture(
            input_type="url",
            source_platform="xiaohongshu",
            content_type="image_article",
            url="https://www.xiaohongshu.com/explore/live-note",
        )
        updated = store_fs.update_capture(
            capture.id,
            status="needs_user_action",
            result_state="awaiting_user",
            error_stage="extract",
            error_message="当前页面触发了平台验证，请完成验证后继续。",
            processing=capture.processing.model_copy(
                update={
                    "current_stage": "awaiting_user",
                    "progress_percent": 18,
                    "failure_reason_code": "browser_challenge_required",
                    "retryable": True,
                }
            ),
        )

        response = self.client.get(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["capture"]
        self.assertEqual(updated.status, "needs_user_action")
        self.assertEqual(payload["status"], "needs_user_action")
        self.assertEqual(payload["result_state"], "awaiting_user")
        self.assertEqual(payload["failure_reason_code"], "browser_challenge_required")

    def test_legacy_platform_failure_is_normalized_at_api_boundary(self) -> None:
        capture = store_fs.create_capture(
            input_type="url",
            source_platform="xiaohongshu",
            content_type="image_article",
            url="https://www.xiaohongshu.com/explore/legacy-challenge",
        )
        store_fs.update_capture(
            capture.id,
            status="failed",
            result_state="failed",
            error_stage="extract",
            error_message="当前页面触发了平台验证，请刷新项目级浏览器会话后再试。",
            processing=capture.processing.model_copy(
                update={
                    "current_stage": "failed",
                    "progress_percent": 0,
                    "failure_reason_code": "browser_challenge_required",
                    "retryable": True,
                }
            ),
        )

        response = self.client.get(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["capture"]
        self.assertEqual(payload["status"], "needs_user_action")
        self.assertEqual(payload["result_state"], "awaiting_user")
        self.assertEqual(payload["current_stage"], "awaiting_user")
        self.assertEqual(payload["progress_percent"], 18)
        self.assertEqual(payload["failure_reason_code"], "browser_challenge_required")

        events_response = self.client.get(f"/v1/captures/{capture.id}/events")
        self.assertEqual(events_response.status_code, 200)
        self.assertIn('"status": "needs_user_action"', events_response.text)

    def test_public_capture_projection_never_exposes_legacy_failed_status(self) -> None:
        capture = store_fs.create_capture(
            input_type="url",
            source_platform="generic_web",
            content_type="webpage",
            url="https://example.com/public-status-projection",
        )
        capture.status = "failed"

        envelope = capture_main._to_capture_envelope(capture)
        history_item = capture_main._public_capture(capture)

        self.assertEqual(envelope.capture.status, "needs_user_action")
        self.assertEqual(envelope.capture.result_state, "awaiting_user")
        self.assertEqual(history_item.status, "needs_user_action")

    def test_missing_capture_endpoints_use_calm_public_copy(self) -> None:
        requests = [
            ("get", "/v1/captures/missing"),
            ("get", "/v1/captures/missing/events"),
            ("post", "/v1/captures/missing/retry"),
            ("get", "/v1/captures/missing/artifacts/txt"),
            ("get", "/v1/captures/missing/artifacts/txt/preview"),
            ("get", "/v1/captures/missing/images/1/thumbnail"),
            ("post", "/v1/captures/missing/images/1/retry"),
            ("post", "/v1/captures/missing/cover"),
        ]

        for method, path in requests:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path)
                detail = response.json()["detail"]
                self.assertNotIn("not found", detail.lower())
                self.assertNotIn("missing", detail.lower())
                self.assertNotIn("failed", detail.lower())
                self.assertRegex(detail, r"[\u4e00-\u9fff]")

    def test_legacy_retryable_failure_is_normalized_to_recoverable_state(self) -> None:
        capture = store_fs.create_capture(
            input_type="url",
            source_platform="generic_web",
            content_type="webpage",
            url="https://example.com/temporary-network-error",
        )
        store_fs.update_capture(
            capture.id,
            status="failed",
            result_state="failed",
            error_stage="extract",
            error_message="当前网络环境没有稳定连上来源站点，请稍后重试。",
            processing=capture.processing.model_copy(
                update={
                    "current_stage": "failed",
                    "failure_reason_code": "source_fetch_failed",
                    "retryable": True,
                }
            ),
        )

        response = self.client.get(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["capture"]
        self.assertEqual(payload["status"], "needs_user_action")
        self.assertEqual(payload["result_state"], "awaiting_user")
        self.assertEqual(payload["current_stage"], "awaiting_user")
        self.assertEqual(payload["failure_reason_code"], "source_fetch_failed")
        self.assertTrue(payload["retryable"])

    def test_public_capture_error_message_hides_raw_failure_details(self) -> None:
        capture = store_fs.create_capture(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://www.youtube.com/watch?v=public-error-copy",
        )
        store_fs.update_capture(
            capture.id,
            status="needs_user_action",
            result_state="awaiting_user",
            error_stage="download",
            error_message="媒体下载失败：HTTP Error 500 /tmp/private-token",
            processing=capture.processing.model_copy(
                update={
                    "current_stage": "awaiting_user",
                    "progress_percent": 18,
                    "failure_reason_code": "download_failed",
                    "retryable": True,
                }
            ),
        )

        response = self.client.get(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        message = response.json()["capture"]["error_message"]
        self.assertNotIn("失败", message)
        self.assertNotIn("HTTP Error", message)
        self.assertNotIn("private-token", message)
        self.assertIn("处理暂时中断", message)

    def test_legacy_permanent_failure_is_exposed_as_unavailable_attention(self) -> None:
        capture = store_fs.create_capture(
            input_type="url",
            source_platform="youtube",
            content_type="video",
            url="https://www.youtube.com/watch?v=unavailable",
        )
        store_fs.update_capture(
            capture.id,
            status="failed",
            result_state="failed",
            error_stage="extract",
            error_message="这条内容当前已经不可访问，请换一条公开内容再试。",
            processing=capture.processing.model_copy(
                update={
                    "current_stage": "failed",
                    "failure_reason_code": "source_not_found",
                    "retryable": False,
                }
            ),
        )

        response = self.client.get(f"/v1/captures/{capture.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["capture"]
        self.assertEqual(payload["status"], "needs_user_action")
        self.assertEqual(payload["result_state"], "awaiting_user")
        self.assertEqual(payload["current_stage"], "awaiting_user")
        self.assertEqual(payload["failure_reason_code"], "source_not_found")
        self.assertFalse(payload["retryable"])

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

    def test_browser_session_controls_are_local_only(self) -> None:
        with patch.object(capture_main, "refresh_browser_runtime") as refresh, patch.object(
            capture_main.subprocess, "Popen"
        ) as popen:
            popen.return_value.poll.return_value = None
            refresh_response = self.client.post("/v1/runtime/browser/refresh")
            session_response = self.client.post("/v1/runtime/browser/session")

        capture_main._browser_session_process = None

        self.assertEqual(refresh_response.status_code, 200)
        self.assertEqual(session_response.status_code, 200)
        refresh.assert_called()
        popen.assert_called_once()
        self.assertEqual(session_response.json()["status"], "opened")

        with patch.object(capture_main, "RUNTIME_TARGET", "cloud_demo"):
            self.assertEqual(
                self.client.post("/v1/runtime/browser/refresh").status_code,
                404,
            )
            self.assertEqual(
                self.client.post("/v1/runtime/browser/session").status_code,
                404,
            )

    def test_browser_session_reports_immediate_process_exit(self) -> None:
        try:
            with patch.object(capture_main, "refresh_browser_runtime"), patch.object(
                capture_main.subprocess, "Popen"
            ) as popen:
                popen.return_value.poll.return_value = 1
                response = self.client.post("/v1/runtime/browser/session")
        finally:
            capture_main._browser_session_process = None

        self.assertEqual(response.status_code, 503)

    def test_browser_session_passes_target_through_local_handoff_file(self) -> None:
        target = "https://www.xiaohongshu.com/explore/demo"
        target_file = None
        try:
            with patch.object(capture_main, "refresh_browser_runtime"), patch.object(
                capture_main.subprocess, "Popen"
            ) as popen:
                popen.return_value.poll.return_value = None
                response = self.client.post(
                    "/v1/runtime/browser/session",
                    json={"url": target},
                )
                command = popen.call_args.args[0]
                target_file = Path(command[command.index("--target-file") + 1])
                self.assertEqual(json.loads(target_file.read_text(encoding="utf-8"))["url"], target)
        finally:
            if target_file:
                target_file.unlink(missing_ok=True)
            capture_main._browser_session_process = None

        self.assertEqual(response.status_code, 200)

        invalid = self.client.post(
            "/v1/runtime/browser/session",
            json={"url": "https://example.com/private"},
        )
        self.assertEqual(invalid.status_code, 400)

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

    def test_frontend_entrypoint_is_not_cached(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers.get("cache-control", ""))

    def test_health_and_config_are_not_cached(self) -> None:
        for path in ("/health", "/config"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response.headers.get("cache-control", ""))

    def test_hashed_frontend_assets_are_immutable(self) -> None:
        manifest = json.loads(capture_main.FRONTEND_BUILD_MANIFEST.read_text(encoding="utf-8"))
        asset = next(item for item in manifest["output_files"] if item.startswith("assets/"))

        response = self.client.get(f"/{asset}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("cache-control"),
            "public, max-age=31536000, immutable",
        )

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
        self.assertTrue(payload["frontend_build_id"])
        self.assertTrue(payload["frontend_git_sha"])
        self.assertEqual(payload["asset_manifest_status"], "ready")
        self.assertNotIn("run_mode", payload)
        self.assertIsInstance(payload["components"], dict)
        self.assertIn(payload["browser_runtime"]["state"], {"idle", "ready", "restarting"})
        self.assertIn("selected_provider", payload["transcription_runtime"])
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn("browser-profile", serialized)
        self.assertNotIn("api_key", serialized)

    def test_brand_mark_is_served_from_the_built_frontend(self) -> None:
        response = self.client.get("/brand-mark.svg")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("image/svg+xml"))
        self.assertIn(b'<svg xmlns="http://www.w3.org/2000/svg"', response.content)

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
        self.assertEqual(payload["artifacts"][0]["preview_url"], f"/v1/captures/{capture.id}/artifacts/txt/preview")
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
        ready_path = self.artifacts_dir / capture.id / "image_01.jpg"
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_bytes(b"image")
        thumbnail_path = ready_path.with_name("image_01.thumb.v1.jpg")
        thumbnail_path.write_bytes(b"thumbnail")
        result = ResultDocumentModel(
            primary_text="图文正文",
            primary_result_type="article",
            artifacts=[
                ArtifactModel(
                    type="image_01",
                    label="图片 1",
                    path=str(ready_path),
                    download_url=f"/v1/captures/{capture.id}/artifacts/image_01",
                    mime_type="image/jpeg",
                    width=1600,
                    height=1000,
                    thumbnail_path=str(thumbnail_path),
                ),
                ArtifactModel(
                    type="image_02",
                    label="图片 2",
                    path=str(self.artifacts_dir / capture.id / "image_02.jpg"),
                    download_url=f"/v1/captures/{capture.id}/artifacts/image_02",
                    mime_type="image/jpeg",
                    status="failed",
                    optional=True,
                ),
            ],
        )
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
        self.assertEqual(payload["source"]["images"][0]["delivery_status"], "ready")
        self.assertEqual(
            payload["source"]["images"][0]["preview_url"],
            f"/v1/captures/{capture.id}/artifacts/image_01/preview",
        )
        self.assertEqual(payload["source"]["images"][0]["width"], 1600)
        self.assertEqual(payload["source"]["images"][0]["height"], 1000)
        thumbnail_url = payload["source"]["images"][0]["thumbnail_url"]
        self.assertTrue(thumbnail_url.startswith(f"/v1/captures/{capture.id}/images/1/thumbnail?v="))
        thumbnail_response = self.client.get(thumbnail_url)
        self.assertEqual(thumbnail_response.status_code, 200)
        self.assertEqual(thumbnail_response.content, b"thumbnail")
        self.assertEqual(thumbnail_response.headers["cache-control"], "private, max-age=31536000, immutable")
        self.assertIsNone(payload["source"]["images"][1]["live_photo_video_url"])
        self.assertEqual(payload["source"]["images"][1]["delivery_status"], "failed")
        self.assertIsNone(payload["source"]["images"][1]["preview_url"])

    def test_capture_envelope_exposes_timed_result_segments(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="xiaoyuzhou",
            content_type="audio",
            url="https://www.xiaoyuzhoufm.com/episode/demo",
        )
        result = ResultDocumentModel(
            primary_text="第一段",
            transcript_text="第一段",
            primary_result_type="transcript",
            text_source="transcript",
            segments=[
                ResultSegmentModel(
                    kind="timed_text",
                    origin="transcript",
                    text="第一段",
                    start_sec=0.0,
                    end_sec=12.5,
                )
            ],
        )
        capture_main.repository.update(capture.id, status="done", result=result)

        payload = self.client.get(f"/v1/captures/{capture.id}").json()

        self.assertEqual(
            payload["result"]["segments"],
            [
                {
                    "kind": "timed_text",
                    "origin": "transcript",
                    "text": "第一段",
                    "start_sec": 0.0,
                    "end_sec": 12.5,
                }
            ],
        )

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
        self.assertEqual(response.json()["detail"], "这条内容已在准备中，请稍候。")

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
        self.assertIn("attachment", response.headers.get("content-disposition", ""))

        preview = self.client.get(f"/v1/captures/{capture.id}/artifacts/source_media/preview")

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.content, b"video-data")
        self.assertNotIn("attachment", preview.headers.get("content-disposition", ""))
        self.assertEqual(preview.headers.get("cache-control"), "private, max-age=3600")
        self.assertEqual(preview.headers.get("x-content-type-options"), "nosniff")

        ranged = self.client.get(
            f"/v1/captures/{capture.id}/artifacts/source_media/preview",
            headers={"Range": "bytes=0-4"},
        )
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, b"video")
        self.assertEqual(ranged.headers.get("content-range"), "bytes 0-4/10")

    def test_artifact_preview_rejects_failed_file(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="xiaohongshu",
            content_type="image_article",
            url="https://example.com/note",
        )
        failed = ArtifactModel(
            type="image_01",
            label="图片 1",
            path=str(self.artifacts_dir / capture.id / "image_01.jpg"),
            download_url=f"/v1/captures/{capture.id}/artifacts/image_01",
            mime_type="image/jpeg",
            status="failed",
        )
        capture_main.repository.update(
            capture.id,
            status="done",
            result=ResultDocumentModel(primary_text="body", artifacts=[failed]),
        )

        response = self.client.get(f"/v1/captures/{capture.id}/artifacts/image_01/preview")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("not found", response.json()["detail"].lower())
        self.assertNotIn("failed", response.json()["detail"].lower())

        thumbnail = self.client.get(f"/v1/captures/{capture.id}/images/1/thumbnail")

        self.assertEqual(thumbnail.status_code, 404)
        self.assertNotIn("not found", thumbnail.json()["detail"].lower())
        self.assertNotIn("failed", thumbnail.json()["detail"].lower())

    def test_cover_endpoint_backfills_and_persists_legacy_podcast_cover(self) -> None:
        capture = capture_main.repository.create(
            input_type="url",
            source_platform="xiaoyuzhou",
            content_type="audio",
            url="https://www.xiaoyuzhoufm.com/episode/demo",
        )
        source = SourceMetaModel(
            platform="xiaoyuzhou",
            content_type="audio",
            canonical_url="https://www.xiaoyuzhoufm.com/episode/demo",
            thumbnail_url="https://example.com/cover.jpg",
        )
        capture = capture_main.repository.update(
            capture.id,
            status="done",
            source=source,
            result=ResultDocumentModel(
                primary_text="podcast transcript",
                primary_result_type="transcript",
                text_source="transcript",
            ),
        )
        cover_path = self.artifacts_dir / capture.id / "cover_image.jpg"
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(b"cover")

        def backfill(current):
            current.result.artifacts.append(
                ArtifactModel(
                    type="cover_image",
                    label="节目封面",
                    path=str(cover_path),
                    download_url=f"/v1/captures/{current.id}/artifacts/cover_image",
                    preview_url=f"/v1/captures/{current.id}/artifacts/cover_image/preview",
                    mime_type="image/jpeg",
                )
            )
            return current.result

        with patch.object(capture_main, "ensure_capture_cover", side_effect=backfill):
            response = self.client.post(f"/v1/captures/{capture.id}/cover")

        self.assertEqual(response.status_code, 200)
        artifacts = response.json()["artifacts"]
        self.assertEqual([item["type"] for item in artifacts], ["cover_image"])
        stored = capture_main.repository.get(capture.id)
        self.assertEqual(stored.result.artifacts[0].type, "cover_image")

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
            with (
                patch.object(capture_main.repository, "create", side_effect=RuntimeError("boom")),
                patch.object(capture_main.logger, "exception") as log_exception,
            ):
                response = error_client.post("/v1/captures", json={"text": "https://www.douyin.com/video/7624387162426887464"})
                log_exception.assert_called_once()
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

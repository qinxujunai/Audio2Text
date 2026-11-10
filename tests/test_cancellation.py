from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.pipeline as pipeline
from app import cancellation
from app.schemas import SourceMetaModel


def _capture(capture_id: str, status: str = "queued"):
    url = "https://www.bilibili.com/video/BV1demo/"
    return SimpleNamespace(
        id=capture_id, status=status, result_state="processing", input_type="url", url=url, title=url,
        source_platform="bilibili", content_type="video", started_at=None, finished_at=None,
        error_stage=None, error_message=None,
        source=SourceMetaModel(platform="bilibili", content_type="video", raw_input_text=url),
        processing=pipeline.ProcessingStateModel(),
    )


class CancellationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        pipeline.active_capture_ids.clear()
        pipeline.queued_capture_ids.clear()

    def _apply(self, capture):
        def update(_id, **fields):
            for key, value in fields.items():
                setattr(capture, key, value)
            return capture
        return update

    def test_cancelling_a_queued_capture_marks_it_cancelled_and_retryable(self) -> None:
        capture = _capture("cancel-queued")
        with patch.object(pipeline.repository, "get", return_value=capture), patch.object(
            pipeline.repository, "update", side_effect=self._apply(capture)
        ):
            self.assertTrue(pipeline.cancel_capture(capture.id))
        self.assertEqual(capture.status, "needs_user_action")
        self.assertEqual(capture.error_stage, "cancelled")
        self.assertEqual(capture.processing.failure_reason_code, "user_cancelled")
        self.assertTrue(capture.processing.retryable)

    def test_finished_capture_cannot_be_cancelled(self) -> None:
        capture = _capture("cancel-done", status="done")
        with patch.object(pipeline.repository, "get", return_value=capture):
            self.assertFalse(pipeline.cancel_capture(capture.id))

    def test_a_running_capture_stops_at_its_next_progress_update(self) -> None:
        capture = _capture("cancel-running")
        resolved = SimpleNamespace(normalized_url=capture.url, platform="bilibili", content_type="video",
                                   input_warning="", cleaned_input=capture.url, detected_urls=[capture.url],
                                   selection_reason="direct_url")

        def extract(*_args, progress_callback=None, **_kwargs):
            pipeline.cancel_capture(capture.id)  # the person presses 取消 while it downloads
            progress_callback(40, "正在下载音频。")
            raise AssertionError("the worker should have stopped")

        adapter = SimpleNamespace(extract=extract)
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline.repository, "get", return_value=capture), patch.object(
            pipeline.repository, "update", side_effect=self._apply(capture)
        ), patch.object(pipeline.repository, "capture_workspace", return_value=Path(tmp)), patch.object(
            pipeline, "ARTIFACTS_DIR", Path(tmp) / "artifacts"
        ), patch.object(pipeline, "resolve_url", return_value=resolved), patch.object(
            pipeline, "get_url_adapter", return_value=adapter
        ):
            pipeline.process_capture(capture.id)

        self.assertEqual(capture.status, "needs_user_action")
        self.assertEqual(capture.processing.failure_reason_code, "user_cancelled")
        self.assertFalse(cancellation.is_cancelled(capture.id), "the request is cleared once handled")
        self.assertNotIn(capture.id, pipeline.active_capture_ids)

    def test_cancel_terminates_a_running_download_process(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        stopped = []
        try:
            with cancellation.working_on("cancel-process"):
                with cancellation.tracked_process(process, lambda p: (stopped.append(p.pid), p.kill())):
                    cancellation.request_cancel("cancel-process")
                    process.wait(timeout=10)
            self.assertEqual(stopped, [process.pid])
        finally:
            if process.poll() is None:
                process.kill()
            cancellation.clear("cancel-process")

    def test_cancelled_is_not_swallowed_by_generic_fallbacks(self) -> None:
        with cancellation.working_on("cancel-fallback"):
            cancellation.request_cancel("cancel-fallback")
            with self.assertRaises(cancellation.CaptureCancelled):
                try:
                    cancellation.raise_if_cancelled()
                except Exception:  # noqa: BLE001 - this is exactly what must not catch it
                    self.fail("CaptureCancelled was caught by except Exception")
        cancellation.clear("cancel-fallback")


if __name__ == "__main__":
    unittest.main()

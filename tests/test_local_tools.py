from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import local_preferences as prefs
from app.proofreading import ProofreadingService, apply_edits, chunks, text_hash, validate_edits, config_identity
from app import local_tools_api
from scripts import model_loader


class LocalToolsTests(unittest.TestCase):
    def test_credential_encrypted_and_roundtrips_on_windows(self):
        import os
        if os.name != "nt":
            self.skipTest("Windows DPAPI")
        encrypted = prefs._credential("test-private-key")
        self.assertNotIn("test-private-key", encrypted)
        self.assertEqual(prefs._credential(encrypted, decrypt=True), "test-private-key")

    def test_preferences_do_not_reuse_key_at_another_endpoint(self):
        with tempfile.TemporaryDirectory() as root, patch.object(prefs, "WORKSPACE_DIR", Path(root)):
            prefs.save_preferences(device="cpu", model_path="", base_url="https://first.example/v1", api_key="private", model="text")
            prefs.save_preferences(device="cpu", model_path="", base_url="https://second.example/v1", api_key=None, model="text")
            self.assertFalse(prefs.read_preferences()["proofreading"]["encrypted_key"])

    def test_invalid_addresses_rejected(self):
        for target in ["http://remote.example/v1", "https://key@host/v1", "https://host/v1?key=private", "file:///x"]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                prefs.normalize_base_url(target)
        self.assertEqual(prefs.normalize_base_url("http://127.0.0.1:1234/v1/"), "http://127.0.0.1:1234/v1")

    def test_device_switch_retains_both_sensevoice_model_paths(self):
        with tempfile.TemporaryDirectory() as root, patch.object(prefs, "WORKSPACE_DIR", Path(root)), patch.object(prefs, "cuda_available", return_value=True):
            gpu, cpu = Path(root)/"gpu", Path(root)/"cpu"
            for folder, name in ((gpu, "model.onnx"), (cpu, "model.int8.onnx")):
                folder.mkdir(); (folder/name).write_bytes(b"model"); (folder/"tokens.txt").write_bytes(b"tokens")
            for device, folder in (("cuda", gpu), ("cpu", cpu)):
                prefs.save_preferences(device=device, model_path=str(folder), base_url="", api_key=None, model="")
            saved = prefs.read_preferences()["transcription"]
            self.assertEqual(saved["sensevoice_cuda_model_path"], str(gpu.resolve()))
            self.assertEqual(saved["sensevoice_cpu_model_path"], str(cpu.resolve()))

    def test_quantities_units_and_chinese_numbers_cannot_change(self):
        edits = validate_edits("金额三百元。剂量25mg。这里有错别宇。", [
            {"before": "三百元", "after": "四百元"}, {"before": "25mg", "after": "25kg"},
            {"before": "错别宇", "after": "错别字"}])
        self.assertEqual([edit["before"] for edit in edits], ["错别宇"])

    def test_rejects_numbers_omissions_rewrites_and_ambiguous_matches(self):
        text = "营收123万元。重复。重复。今天我们在这里分享很多经验。错别宇。"
        edits = validate_edits(text, [
            {"before": "123万元", "after": "128万元"},
            {"before": "重复。", "after": "重复！"},
            {"before": "今天我们在这里分享很多经验。", "after": "分享经验。"},
            {"before": "错别宇", "after": "错别字"},
            {"before": "很多经验", "after": ""},
        ])
        self.assertEqual(len(edits), 1)
        result = apply_edits(text, edits)
        self.assertEqual(result, text.replace("错别宇", "错别字"))
        self.assertIn("重复。重复。", result)

    def test_overlap_and_changed_source_rejected(self):
        text = "错别宇内容"
        edits = validate_edits(text, [{"before": "错别宇", "after": "错别字"}, {"before": "宇内容", "after": "字内容"}])
        self.assertEqual(len(edits), 1)
        with self.assertRaises(ValueError):
            apply_edits("已经变了", edits)

    def test_long_text_chunks_preserve_every_character(self):
        text = ("内容和数字123。\n" * 1000) + "结束"
        pieces = list(chunks(text))
        self.assertEqual("".join(piece for _, piece in pieces), text)
        self.assertTrue(all(len(piece) <= 2600 for _, piece in pieces))

    @patch("app.proofreading.proofreading_config", return_value={"model": "mock"})
    def test_service_saves_separately_and_guards_original_hash(self, _config):
        with tempfile.TemporaryDirectory() as root:
            result = SimpleNamespace(primary_text="这里有错别宇。\n保留原话。")
            capture = SimpleNamespace(status="done", result=result)
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root))
            service = ProofreadingService(repo)
            source = result.primary_text
            service.jobs["cap"] = {"id": "current", "config_hash": config_identity({"model": "mock"}), "status": "ready", "edits": validate_edits(source, [{"before": "错别宇", "after": "错别字"}]), "source_hash": text_hash(source), "model": "mock"}
            saved = service.accept("cap", [0], job_id="current")
            self.assertEqual(result.primary_text, source)
            self.assertEqual(saved["text"], "这里有错别字。\n保留原话。")
            self.assertEqual(service.saved("cap")["text"], saved["text"])
            result.primary_text = "新内容"
            self.assertIsNone(service.saved("cap"))
            with self.assertRaises(ValueError):
                service.accept("cap", [0])
            service.executor.shutdown()

    @patch("app.proofreading.proofreading_config", return_value={"model": "mock"})
    def test_cancelled_task_cannot_write_ready_or_save_partial_result(self, _config):
        with tempfile.TemporaryDirectory() as root:
            repo = SimpleNamespace(get=lambda _: SimpleNamespace(status="done", result=SimpleNamespace(primary_text="错别宇")), capture_workspace=lambda _: Path(root))
            service = ProofreadingService(repo)
            entered, release = threading.Event(), threading.Event()
            def request(*args):
                entered.set(); release.wait(2)
                return [{"before": "错别宇", "after": "错别字"}]
            with patch("app.proofreading.proofreading_config", return_value={"model": "mock"}), patch("app.proofreading.request_edits", side_effect=request):
                service.start("cap")
                self.assertTrue(entered.wait(1))
                service.cancel("cap"); release.set(); service.executor.shutdown()
            self.assertEqual(service.status("cap")["job"]["status"], "cancelled")
            self.assertIsNone(service.saved("cap"))

    def test_cuda_does_not_fallback_after_load_error(self):
        options = {"device": "cuda", "compute_type": "int8_float16", "model_path": Path.cwd()}
        with patch.object(model_loader, "transcription_options", return_value=options), patch.object(model_loader, "_model", None), patch.object(model_loader, "configure_cuda_runtime_search_path"), patch.object(model_loader, "WhisperModel", side_effect=RuntimeError("DLL absent")) as mocked:
            with self.assertRaisesRegex(RuntimeError, "未切换到 CPU"):
                model_loader.get_model()
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(mocked.call_args.kwargs["device"], "cuda")

    def test_explicit_sensevoice_cuda_keeps_the_selected_model(self):
        from app.providers import transcription
        options = {"device": "cuda", "engine": "sensevoice", "compute_type": "float32", "model_path": Path("missing"), "sdk_path": Path("sdk")}
        with patch.object(prefs, "transcription_options", return_value=options), patch.object(transcription, "TRANSCRIPTION_PROVIDER", "auto"), patch.object(transcription, "DEPLOYMENT_MODE", "local"):
            provider = transcription.get_transcription_provider(platform="bilibili")
        self.assertIsInstance(provider, transcription.LocalSenseVoiceCudaProvider)
        self.assertEqual(provider.model_dir, Path("missing"))

    def test_cuda_worker_missing_components_never_decodes_on_cpu(self):
        from app.sensevoice_cuda import SenseVoiceCudaWorker
        with tempfile.TemporaryDirectory() as root:
            worker = SenseVoiceCudaWorker(Path(root), Path(root))
            with self.assertRaisesRegex(RuntimeError, "未切换到 CPU"):
                worker.warm()
            self.assertIsNone(worker.process)

    def test_selected_cuda_preferences_drive_readiness_without_cpu_model(self):
        from app import main
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            for filename in ("model.onnx", "tokens.txt", "sdk/sherpa_onnx/lib/onnxruntime_providers_cuda.dll"):
                path = directory / filename; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"fixture")
            options = {"engine": "sensevoice", "execution_device": "cuda", "model_path": directory, "sdk_path": directory / "sdk"}
            with patch.object(prefs, "transcription_options", return_value=options), patch.object(main, "TRANSCRIPTION_PROVIDER", "auto"), patch.object(main, "DEPLOYMENT_MODE", "local"), patch.object(main, "TRANSCRIPTION_AVAILABLE", False):
                self.assertTrue(main._selected_transcription_available())
                self.assertEqual(main._component_status()["model"], "ready")
                (directory / "sdk/sherpa_onnx/lib/onnxruntime_providers_cuda.dll").unlink()
                self.assertFalse(main._selected_transcription_available())

    def test_cuda_batch_preserves_order_and_timeline(self):
        from app.providers import transcription
        worker = SimpleNamespace(warm=lambda: {}, decode=lambda parts: [f"片段{len(p)}" for p in parts])
        with tempfile.TemporaryDirectory() as root:
            media = Path(root) / "audio.wav"; media.write_bytes(b"fixture")
            with patch("app.sensevoice_cuda.get_worker", return_value=worker):
                provider = transcription.LocalSenseVoiceCudaProvider(Path(root), Path(root))
            parts = [[0.0]*16000, [0.0]*32000, [0.0]*16000, [0.0]*16000, [0.0]*16000]
            with patch.object(provider, "_iter_audio_chunks", return_value=parts), patch("app.providers.transcription._probe_audio_duration", return_value=6):
                result = provider.transcribe(str(media), capture_id="cap", output_folder=root)
            self.assertEqual(result.segment_count, 5)
            self.assertEqual(result.timeline_text.splitlines()[-1], "5.000s -> 6.000s 片段16000")
            self.assertEqual(transcription._transcription_runtime["actual_device"], "cuda")

    def test_local_api_rejects_cross_origin_and_cloud(self):
        app = FastAPI(); app.include_router(local_tools_api.router)
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/local/preferences", headers={"Origin": "https://evil.example"}).status_code, 403)
            with patch.object(local_tools_api, "DEPLOYMENT_MODE", "cloud_preview"):
                self.assertEqual(client.get("/v1/local/preferences").status_code, 403)

    def test_preferences_response_never_exposes_key(self):
        with tempfile.TemporaryDirectory() as root, patch.object(prefs, "WORKSPACE_DIR", Path(root)):
            prefs.save_preferences(device="cpu", model_path="", base_url="https://host.example/v1", api_key="private-test-key", model="mock")
            response = local_tools_api.preferences()
            self.assertTrue(response["has_api_key"])
            self.assertNotIn("private-test-key", json.dumps(response))
            self.assertNotIn("encrypted_key", json.dumps(response))

    def test_webview_cross_site_requires_exact_desktop_origin(self):
        from starlette.requests import Request
        from fastapi import HTTPException
        def request(origin):
            return Request({"type": "http", "method": "GET", "scheme": "http", "path": "/v1/local/preferences", "query_string": b"", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 1234), "headers": [(b"origin", origin.encode()), (b"sec-fetch-site", b"cross-site")]})
        with patch.object(local_tools_api, "RUNTIME_TARGET", "windows_desktop"):
            local_tools_api.local_only(request("http://tauri.localhost"))
            with self.assertRaises(HTTPException):
                local_tools_api.local_only(request("https://evil.example"))
        with patch.object(local_tools_api, "RUNTIME_TARGET", "web"):
            with self.assertRaises(HTTPException):
                local_tools_api.local_only(request("http://tauri.localhost"))


if __name__ == "__main__":
    unittest.main()

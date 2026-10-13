"""Exercise the packaged backend, actual CUDA upload, and local model HTTP flow."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import uuid

import httpx
from app.local_preferences import _credential
from tests.ui_local_tools_suite import ModelFixture
from tests.ui_smoke_suite import _video_capture, _seed_capture
from http.server import ThreadingHTTPServer
from scripts.build_fingerprint import resource_fingerprint


class VerificationDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        # Windows can retain a child worker's inherited log handle briefly
        # after its parent exits. Retry only our exact temporary directory.
        for attempt in range(20):
            try:
                return super().cleanup()
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, default=Path("web/src-tauri/sidecar/wanxiang-api/wanxiang-api.exe"))
    parser.add_argument("--output", type=Path, default=Path("tests_runtime/cuda-proofreading-verification/packaged-backend.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    model_server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
    threading.Thread(target=model_server.serve_forever, daemon=True).start()
    report = {"executable": str(args.executable.resolve()), "sha256": hashlib.sha256(args.executable.read_bytes()).hexdigest()}
    resources = resource_fingerprint(args.executable.parent)
    report.update(resources)
    with VerificationDirectory(prefix="wanxiang-native-verify-", dir=root / "tests_runtime") as directory:
        workspace = Path(directory) / "workspace"
        capture = _video_capture(workspace, "proofing0001", created_at="2026-10-11T01:00:00", title="本地校对验收")
        capture.result.primary_text = "这里有错别宇。金额123元。\n全部内容保留。"
        capture.result.views.primary = capture.result.primary_text
        _seed_capture(workspace, capture)
        prefs = {"transcription": {"engine": "sensevoice", "device": "cuda",
            "model_path": str(root / "workspace/runtime/models/sensevoice/small-fp32"),
            "sdk_path": str(root / "workspace/runtime/candidates/sensevoice-cuda/site")},
            "proofreading": {"base_url": f"http://127.0.0.1:{model_server.server_port}/v1",
                             "encrypted_key": _credential("fixture-local-key"), "model": "fixture-text-model"}}
        (workspace / "local-preferences.json").write_text(json.dumps(prefs), encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        token = uuid.uuid4().hex
        env = dict(os.environ)
        env.update(AUDIO2TEXT_WORKSPACE_DIR=str(workspace), AUDIO2TEXT_API_PORT=str(port),
            AUDIO2TEXT_API_HOST="127.0.0.1", AUDIO2TEXT_RUNTIME_TARGET="windows_desktop",
            AUDIO2TEXT_DESKTOP_TOKEN=token, AUDIO2TEXT_TRANSCRIPTION_PROVIDER="auto",
            AUDIO2TEXT_DEVICE="auto", AUDIO2TEXT_ALLOW_DEGRADED_START="1",
            AUDIO2TEXT_FFMPEG_PATH=str(root / "workspace/runtime/ffmpeg/ffmpeg.exe"))
        env.pop("AUDIO2TEXT_DESKTOP_PARENT_PID", None)
        log = args.output.with_suffix(".log").open("w", encoding="utf-8")
        process = subprocess.Popen([str(args.executable.resolve())], cwd=args.executable.resolve().parent,
                                   env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        base = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(base_url=base, headers={"X-Wanxiang-Desktop-Token": token}, trust_env=False, timeout=125) as client:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        health = client.get("/health"); health.raise_for_status(); break
                    except httpx.HTTPError:
                        if time.monotonic() > deadline: raise
                        time.sleep(.3)
                report["health"] = health.json()
                version = json.loads((root / "web/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
                assert report["health"]["version"] == version
                assert report["health"]["components"]["model"] == "ready"
                response = client.post("/v1/local/cuda-check", json={}); response.raise_for_status()
                report["cuda_check"] = response.json()
                assert report["cuda_check"]["device"] == "cuda"
                source = root / "tests_runtime/cuda-proofreading-verification/sample-180s.wav"
                started = time.perf_counter()
                with source.open("rb") as handle:
                    response = client.post("/v1/captures", files={"file": ("cuda-180s.wav", handle, "audio/wav")})
                response.raise_for_status(); capture_id = response.json()["capture_id"]
                deadline = time.monotonic() + 90
                while True:
                    response = client.get(f"/v1/captures/{capture_id}"); response.raise_for_status()
                    data = response.json(); status = data["capture"]["status"]
                    if status == "done": break
                    if status in {"failed", "needs_user_action"} or time.monotonic() > deadline:
                        raise RuntimeError(f"Packaged CUDA capture did not complete: {status}")
                    time.sleep(.3)
                runtime = client.get("/v1/local/preferences").json()["runtime"]
                assert runtime["actual_device"] == "cuda" and runtime["active_provider"] == "local_sensevoice_cuda"
                text = data["result"]["primary_text"]
                assert len(text) > 100
                report["upload"] = {"status": status, "elapsed_seconds": time.perf_counter()-started,
                                    "chars": len(text), "runtime": runtime}
                preferences = client.get("/v1/local/preferences").json()
                assert "encrypted_key" not in json.dumps(preferences) and "fixture-local-key" not in json.dumps(preferences)
                response = client.post("/v1/local/models", json={"base_url": prefs["proofreading"]["base_url"]})
                response.raise_for_status(); assert len(response.json()["models"]) == 2
                response = client.post("/v1/local/proofreading-check", json={"base_url": prefs["proofreading"]["base_url"], "model": prefs["proofreading"]["model"]})
                response.raise_for_status(); assert response.json()["status"] == "ready"
                response = client.patch("/v1/local/preferences", json={"section": "proofreading", "model": prefs["proofreading"]["model"]})
                response.raise_for_status(); assert response.json()["status"] == "saved"
                assert client.get("/v1/local/preferences").json()["device"] == "cuda"
                response = client.post("/v1/local/proofreading-check", json={"base_url": prefs["proofreading"]["base_url"], "model": "fixture", "api_key": {"secret": "private-validation-fixture"}})
                assert response.status_code == 422 and "private-validation-fixture" not in response.text
                path = workspace / "captures/proofing0001/capture.json"
                before = path.read_bytes()
                response = client.post("/v1/local/captures/proofing0001/proofreading", json={}); response.raise_for_status()
                deadline = time.monotonic() + 15
                while True:
                    response = client.get("/v1/local/captures/proofing0001/proofreading"); response.raise_for_status()
                    job = response.json()["job"]
                    if job["status"] != "running": break
                    if time.monotonic() > deadline: raise RuntimeError("Proofreading timed out")
                    time.sleep(.1)
                assert job["status"] == "ready" and len(job["edits"]) == 1
                response = client.post("/v1/local/captures/proofing0001/proofreading/accept", json={"indices": [0], "job_id": "stale"})
                assert response.status_code == 409
                response = client.post("/v1/local/captures/proofing0001/proofreading/accept", json={"indices": [0], "job_id": job["id"]}); response.raise_for_status()
                assert response.json()["text"] == capture.result.primary_text.replace("错别宇", "错别字")
                for version in ("original", "corrected"):
                    download = client.get(f"/v1/local/captures/proofing0001/proofreading/download?version={version}")
                    download.raise_for_status(); assert download.content.startswith(b"\xef\xbb\xbf")
                assert path.read_bytes() == before
                report["proofreading"] = {"model_discovery": "passed", "retained_edits": 1,
                                          "changed_number_rejected": True, "original_unchanged": True,
                                          "original_and_corrected_downloads": "passed"}
                denied = client.get("/v1/local/preferences", headers={"Origin": "https://external.example"})
                assert denied.status_code == 403
                report["cross_origin_guard"] = "passed"
        finally:
            process.terminate(); process.wait(timeout=10); log.close()
            model_server.shutdown(); model_server.server_close()
    if resource_fingerprint(args.executable.parent) != resources:
        raise RuntimeError("Packaged resources changed during acceptance; report discarded.")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Packaged CUDA upload, model discovery, conservative proofreading and both downloads: passed.")


if __name__ == "__main__":
    main()

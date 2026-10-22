"""Persistent, bounded CUDA worker client. Explicit CUDA never silently falls back."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading

from scripts.config import TEMP_DIR, LOGS_DIR
from app.runtime_preflight import apply_cuda_runtime_to_env


class SenseVoiceCudaWorker:
    def __init__(self, model_path: Path, sdk_path: Path, *, device="cuda"):
        self.model_path, self.sdk_path, self.device = model_path, sdk_path, device
        self.lock = threading.RLock()
        self.process = None
        self.responses = queue.Queue()
        self.runtime = {}
        self.log = None
        self.log_path = LOGS_DIR / "sensevoice-cuda-worker.log"
        atexit.register(self.close)

    def close(self):
        with self.lock:
            if self.process:
                process, self.process = self.process, None
                try:
                    process.stdin.close()
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
            self.runtime = {}
            if self.log:
                self.log.close()
                self.log = None

    def _read(self, process, responses):
        try:
            for line in process.stdout:
                responses.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            responses.put({"ok": False})

    def _request(self, message):
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
            response = self.responses.get(timeout=120)
            if not response.get("ok"):
                raise RuntimeError("Worker inference failed")
            # The upstream SDK can silently use CPU when the build lacks CUDA.
            # Reject that behavior in addition to checking the loaded GPU DLL.
            if self.device == "cuda" and "fallback to cpu" in self.log_path.read_text(encoding="utf-8", errors="replace").lower():
                raise RuntimeError("Silent CPU fallback rejected")
            return response
        except (OSError, queue.Empty, RuntimeError) as exc:
            self.close()
            raise RuntimeError("SenseVoice CUDA 未就绪，请检查模型与运行组件；未切换到 CPU。") from exc

    def warm(self):
        with self.lock:
            if self.process and self.process.poll() is None and self.runtime:
                return dict(self.runtime)
            self.close()
            if not all((self.model_path / name).is_file() for name in ("model.onnx", "tokens.txt")):
                raise RuntimeError("SenseVoice CUDA 模型尚未准备完整；未切换到 CPU。")
            if not (self.sdk_path / "sherpa_onnx" / "lib" / "onnxruntime_providers_cuda.dll").is_file():
                raise RuntimeError("SenseVoice CUDA 运行组件尚未准备完整；未切换到 CPU。")
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            self.log = self.log_path.open("w", encoding="utf-8")
            command = [sys.executable, "--sensevoice-gpu-worker"] if getattr(sys, "frozen", False) else [
                sys.executable, "-m", "scripts.sensevoice_gpu_worker"]
            env = dict(os.environ)
            apply_cuda_runtime_to_env(env)
            env["PYTHONIOENCODING"] = "utf-8"
            self.responses = queue.Queue()
            try:
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=self.log, text=True, encoding="utf-8", env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except OSError:
                self.close()
                raise
            threading.Thread(target=self._read, args=(self.process, self.responses), daemon=True).start()
            response = self._request({"op": "load", "model_path": str(self.model_path),
                                      "sdk_path": str(self.sdk_path), "device": self.device})
            self.runtime = response["runtime"]
            return dict(self.runtime)

    def decode(self, chunks):
        with self.lock:
            self.warm()
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="sensevoice-cuda-", dir=TEMP_DIR) as root:
                import numpy as np
                files = []
                for index, chunk in enumerate(chunks):
                    filename = Path(root) / f"{index}.f32"
                    np.asarray(chunk, dtype="float32").tofile(filename)
                    files.append(str(filename))
                return self._request({"op": "decode", "files": files})["texts"]


_worker = None
_lock = threading.RLock()


def get_worker(model_path: Path, sdk_path: Path):
    global _worker
    with _lock:
        if _worker is None or (_worker.model_path, _worker.sdk_path) != (model_path, sdk_path):
            if _worker:
                _worker.close()
            _worker = SenseVoiceCudaWorker(model_path, sdk_path)
        return _worker

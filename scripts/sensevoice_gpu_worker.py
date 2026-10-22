"""Isolated official sherpa CUDA runtime; newline JSON on inherited pipes.

The desktop CPU backend and GPU SDK have different ONNX DLLs. They must not
share a process. This worker keeps one recognizer alive until its parent closes.
"""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def _stdio():
    if os.name == "nt" and (sys.stdin is None or sys.stdout is None):
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
        kernel.GetStdHandle.restype = ctypes.c_void_p
        for name, identifier, mode, flags in (("stdin", -10, "r", os.O_RDONLY),
                                               ("stdout", -11, "w", os.O_WRONLY),
                                               ("stderr", -12, "w", os.O_WRONLY)):
            handle = kernel.GetStdHandle(identifier & 0xffffffff)
            if handle and handle != ctypes.c_void_p(-1).value:
                fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
                setattr(sys, name, os.fdopen(fd, mode, encoding="utf-8", buffering=1))
    for handle in (sys.stdin, sys.stdout, sys.stderr):
        if handle and hasattr(handle, "reconfigure"):
            handle.reconfigure(encoding="utf-8")


def load_sdk(site: Path):
    site = site.resolve()
    lib = site / "sherpa_onnx" / "lib"
    if not (lib / "onnxruntime_providers_cuda.dll").is_file():
        raise RuntimeError("CUDA SDK is incomplete")
    # Retain directory handles. Explicitly load the GPU ONNX DLL first so the
    # frozen CPU backend's search directory cannot supply a same-named DLL.
    handles = [os.add_dll_directory(str(lib))] if os.name == "nt" else []
    if os.name == "nt":
        ctypes.WinDLL(str(lib / "onnxruntime.dll"))
    sys.path.insert(0, str(site))
    spec = importlib.util.spec_from_file_location("sherpa_onnx", site / "sherpa_onnx" / "__init__.py",
                                                submodule_search_locations=[str(site / "sherpa_onnx")])
    module = importlib.util.module_from_spec(spec)
    sys.modules["sherpa_onnx"] = module
    spec.loader.exec_module(module)
    return module, handles


def main():
    _stdio()
    recognizer = None
    sdk_handles = []
    runtime = {}
    for line in sys.stdin:
        try:
            message = json.loads(line)
            operation = message.get("op")
            if operation == "load":
                import numpy as np
                sdk, sdk_handles = load_sdk(Path(message["sdk_path"]))
                model = Path(message["model_path"])
                device = message.get("device", "cuda")
                if device not in {"cpu", "cuda"}:
                    raise ValueError("Invalid device")
                started = time.perf_counter()
                recognizer = sdk.OfflineRecognizer.from_sense_voice(
                    model=str(model / "model.onnx"), tokens=str(model / "tokens.txt"),
                    num_threads=2, provider=device, language="auto", use_itn=True)
                stream = recognizer.create_stream()
                stream.accept_waveform(16000, np.zeros(16000, dtype=np.float32))
                recognizer.decode_stream(stream)
                if device == "cuda" and os.name == "nt":
                    kernel = ctypes.WinDLL("kernel32")
                    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
                    kernel.GetModuleHandleW.restype = ctypes.c_void_p
                    if not kernel.GetModuleHandleW("onnxruntime_providers_cuda.dll"):
                        raise RuntimeError("CUDA provider was not loaded; CPU fallback rejected")
                runtime = {"device": device, "compute_type": "float32", "model": "SenseVoiceSmall",
                           "engine_version": sdk.__version__, "pid": os.getpid(),
                           "load_seconds": time.perf_counter() - started}
                response = {"ok": True, "runtime": runtime}
            elif operation == "decode" and recognizer is not None:
                import numpy as np
                files = message["files"]
                if not 1 <= len(files) <= 4:
                    raise ValueError("Batch size must be between 1 and 4")
                streams = []
                for filename in files:
                    samples = np.fromfile(filename, dtype="float32")
                    if not 0 < samples.size <= 28 * 16000 or not np.isfinite(samples).all():
                        raise ValueError("Invalid audio chunk")
                    stream = recognizer.create_stream()
                    stream.accept_waveform(16000, samples)
                    streams.append(stream)
                started = time.perf_counter()
                recognizer.decode_streams(streams)
                response = {"ok": True, "texts": [str(s.result.text or "").strip() for s in streams],
                            "seconds": time.perf_counter() - started, "runtime": runtime}
            else:
                raise ValueError("Worker is not ready or operation is invalid")
        except Exception as exc:
            # Technical details stay in the local worker log, never in API errors.
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            response = {"ok": False}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

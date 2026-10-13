"""Local-only preferences. Credentials never cross the public response boundary."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from scripts.config import WORKSPACE_DIR, RUNTIME_DIR, MODELS_DIR, SENSEVOICE_MODEL_DIR, TRANSCRIPTION_PROVIDER

_lock = threading.RLock()


@lru_cache(maxsize=1)
def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except (ImportError, RuntimeError):
        return False


def _credential(value: str, *, decrypt: bool = False) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise ValueError("此平台暂不支持保存密钥，请使用 Windows 本地版。")

    class Blob(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("无法读取或保存本机密钥，请重新配置。")
    try:
        output = ctypes.string_at(target.data, target.length)
        return output.decode("utf-8") if decrypt else base64.b64encode(output).decode("ascii")
    finally:
        kernel.LocalFree(target.data)


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def read_preferences() -> dict:
    path = WORKSPACE_DIR / "local-preferences.json"
    with _lock:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (OSError, ValueError) as exc:
            raise ValueError("本地设置无法读取，请检查设置文件后重试。") from exc


def transcription_options(*, device: str, model_path: Path, compute_type: str,
                          desktop: bool = False) -> dict:
    data = read_preferences().get("transcription", {})
    selected_device = data.get("device") or device
    if not data.get("device") and desktop and device == "auto":
        selected_device = "cuda" if cuda_available() else "cpu"
    engine = data.get("engine") or ("sensevoice" if desktop or TRANSCRIPTION_PROVIDER == "local_sensevoice" else "faster_whisper")
    execution_device = selected_device
    if engine == "sensevoice" and selected_device == "auto":
        candidate = Path(data.get("model_path") or MODELS_DIR / "sensevoice" / "small-fp32")
        execution_device = "cuda" if cuda_available() and (candidate / "model.onnx").is_file() else "cpu"
    default_path = (MODELS_DIR / "sensevoice" / "small-fp32" if execution_device == "cuda"
                    else SENSEVOICE_MODEL_DIR) if engine == "sensevoice" else model_path
    selected_path = Path(data.get("model_path") or default_path)
    return {"device": selected_device, "model_path": selected_path,
            "engine": engine, "execution_device": execution_device,
            "sdk_path": Path(data.get("sdk_path") or RUNTIME_DIR / "candidates" / "sensevoice-cuda" / "site"),
            "compute_type": "int8" if selected_device == "cpu" else compute_type}


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("请填写有效的兼容 API 地址，不要包含密钥或查询参数。")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 API 地址须使用 HTTPS，本机服务可以使用 HTTP。")
    return value


def save_preferences(*, device: str, model_path: str, base_url: str,
                     api_key: str | None, model: str, engine: str = "sensevoice", sdk_path: str = "") -> dict:
    if device not in {"auto", "cuda", "cpu"}:
        raise ValueError("请选择有效的处理设备。")
    if device == "cuda" and not cuda_available():
        raise ValueError("没有检测到可用的 NVIDIA 显卡，请检查驱动或选择 CPU。")
    if engine not in {"sensevoice", "faster_whisper"}:
        raise ValueError("请选择有效的识别模型。")
    if model_path:
        path = Path(model_path).expanduser()
        names = ("model.onnx" if device == "cuda" or (device == "auto" and (path / "model.onnx").is_file()) else "model.int8.onnx", "tokens.txt") if engine == "sensevoice" else ("model.bin", "config.json")
        if not path.is_absolute() or not all((path / n).is_file() for n in names):
            raise ValueError("请选择与所选模型及设备匹配的完整本地模型目录。")
        model_path = str(path.resolve())
    if sdk_path:
        sdk = Path(sdk_path)
        required = engine == "sensevoice" and device == "cuda"
        if not sdk.is_absolute() or (required and not (sdk / "sherpa_onnx" / "lib" / "onnxruntime_providers_cuda.dll").is_file()):
            raise ValueError("请选择完整的 SenseVoice CUDA 运行组件目录。")
        sdk_path = str(sdk.resolve())
    base_url = normalize_base_url(base_url) if base_url.strip() else ""
    if len(model) > 200 or len(api_key or "") > 8192:
        raise ValueError("模型名或密钥过长，请检查配置。")
    with _lock:
        data = read_preferences()
        transcription = dict(data.get("transcription", {}))
        if engine == "sensevoice" and model_path:
            key = "sensevoice_cuda_model_path" if (Path(model_path) / "model.onnx").is_file() else "sensevoice_cpu_model_path"
            transcription[key] = model_path
        transcription.update(device=device, model_path=model_path, engine=engine, sdk_path=sdk_path)
        old = data.get("proofreading", {})
        secret = old.get("encrypted_key", "")
        if api_key is not None:
            secret = _credential(api_key.strip())
        # Never carry a saved credential over to a different server silently.
        elif base_url != old.get("base_url", ""):
            secret = ""
        data.update(transcription=transcription,
                    proofreading={"base_url": base_url, "encrypted_key": secret, "model": model.strip(),
                                  "credential_revision": uuid.uuid4().hex if secret != old.get("encrypted_key", "") else old.get("credential_revision", "")})
        atomic_json(WORKSPACE_DIR / "local-preferences.json", data)
    return data


def proofreading_config(*, base_url: str | None = None, api_key: str | None = None,
                        model: str | None = None) -> dict:
    stored = read_preferences().get("proofreading", {})
    target = normalize_base_url(base_url if base_url is not None else stored.get("base_url", ""))
    key = api_key
    if key is None and target == stored.get("base_url"):
        key = _credential(stored.get("encrypted_key", ""), decrypt=True)
    return {"base_url": target, "api_key": (key or "").strip(),
            "model": (model if model is not None else stored.get("model", "")).strip(),
            # Legacy credentials use the randomized DPAPI ciphertext identity, never a plaintext key hash.
            "credential_revision": stored.get("credential_revision") or hashlib.sha256(stored.get("encrypted_key", "").encode()).hexdigest()}


def save_section(section: str, **values) -> None:
    """Update one settings section without validating or replacing the other."""
    if section == "proofreading":
        target = normalize_base_url(values.get("base_url", ""))
        model = values.get("model", "").strip()
        key = values.get("api_key")
        if not model or len(model) > 200 or len(key or "") > 8192:
            raise ValueError("请选择校对模型，并检查密钥。")
        with _lock:
            data = read_preferences()
            old = data.get("proofreading", {})
            secret = old.get("encrypted_key", "") if target == old.get("base_url") else ""
            if key is not None:
                secret = _credential(key.strip())
            data["proofreading"] = {"base_url": target, "encrypted_key": secret, "model": model,
                                   "credential_revision": uuid.uuid4().hex if secret != old.get("encrypted_key", "") else old.get("credential_revision", "")}
            atomic_json(WORKSPACE_DIR / "local-preferences.json", data)
    elif section == "transcription":
        with _lock:
            old = read_preferences().get("proofreading", {})
            save_preferences(**values, base_url=old.get("base_url", ""), api_key=None, model=old.get("model", ""))
    else:
        raise ValueError("请选择要保存的设置。")

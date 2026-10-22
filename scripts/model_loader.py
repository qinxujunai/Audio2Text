from faster_whisper import WhisperModel

from app.runtime_preflight import configure_cuda_runtime_search_path
from app.local_preferences import transcription_options, cuda_available
import os
import threading
from scripts.config import COMPUTE_TYPE, DEVICE, MODEL_PATH
from scripts.logger import get_logger

_model = None
_model_runtime = None
_model_key = None
_model_lock = threading.RLock()
logger = get_logger("model_loader", "transcribe.log")

def _load_model():
    global _model, _model_runtime, _model_key
    options = transcription_options(device=DEVICE, model_path=MODEL_PATH, compute_type=COMPUTE_TYPE,
                                    desktop=os.environ.get("AUDIO2TEXT_RUNTIME_TARGET") == "windows_desktop")
    selected_device = options["device"]
    if selected_device == "auto":
        selected_device = "cuda" if cuda_available() else "cpu"
    selected_compute = "int8" if selected_device == "cpu" else options["compute_type"]
    selected_model = options["model_path"]
    key = (str(selected_model), selected_device, selected_compute)

    if _model is not None and _model_key == key:
        return _model

    if not selected_model.exists():
        raise FileNotFoundError(
            "转写模型尚未准备好，请在设置中选择完整的本地模型目录。"
        )

    if selected_device == "cuda":
        runtime_dirs = configure_cuda_runtime_search_path()
        if runtime_dirs:
            logger.info("CUDA 运行时搜索路径已就绪: " + "; ".join(str(path) for path in runtime_dirs))

    runtime_options = [(selected_device, selected_compute)]
    # An explicit CUDA selection never silently falls back to CPU.

    last_error = None
    for device, compute_type in runtime_options:
        try:
            logger.info(
                f"加载模型: model_path={selected_model} | device={device} | compute_type={compute_type}"
            )
            _model = WhisperModel(
                str(selected_model),
                device=device,
                compute_type=compute_type,
            )
            _model_runtime = {
                "model_path": str(selected_model),
                "device": _model.model.device,
                "compute_type": _model.model.compute_type,
            }
            _model_key = key
            logger.info("实际转写设备: device=%s compute_type=%s", _model_runtime["device"], _model_runtime["compute_type"])
            return _model
        except Exception as exc:
            last_error = exc
            logger.warning(
                "模型加载未完成: device=%s compute_type=%s error_type=%s", device, compute_type, type(exc).__name__
            )

    _model = None
    _model_runtime = None
    _model_key = None
    raise RuntimeError("CUDA 转写引擎未就绪，请检查模型与运行组件；未切换到 CPU。" if selected_device == "cuda"
                       else "本地转写引擎未就绪，请检查运行组件。") from last_error


def get_model():
    with _model_lock:
        return _load_model()


def get_model_runtime() -> dict:
    if _model_runtime is None:
        return {
            "model_path": str(MODEL_PATH),
            "device": DEVICE,
            "compute_type": COMPUTE_TYPE,
        }
    return _model_runtime.copy()

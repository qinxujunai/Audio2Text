from faster_whisper import WhisperModel

from scripts.config import COMPUTE_TYPE, DEVICE, MODEL_PATH
from scripts.logger import get_logger

_model = None
_model_runtime = None
logger = get_logger("model_loader", "transcribe.log")

def get_model():
    global _model, _model_runtime

    if _model is not None:
        return _model

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"模型目录不存在: {MODEL_PATH}。请检查 audio2text.settings.json 或环境变量 AUDIO2TEXT_MODEL_PATH。"
        )

    runtime_options = [(DEVICE, COMPUTE_TYPE)]
    if (DEVICE, COMPUTE_TYPE) != ("cpu", "int8"):
        runtime_options.append(("cpu", "int8"))

    last_error = None
    for device, compute_type in runtime_options:
        try:
            logger.info(
                f"加载模型: model_path={MODEL_PATH} | device={device} | compute_type={compute_type}"
            )
            _model = WhisperModel(
                str(MODEL_PATH),
                device=device,
                compute_type=compute_type,
            )
            _model_runtime = {
                "model_path": str(MODEL_PATH),
                "device": device,
                "compute_type": compute_type,
            }
            return _model
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"模型加载失败，尝试下一组配置: device={device} | compute_type={compute_type} | error={exc}"
            )

    raise RuntimeError(f"模型加载失败: {last_error}") from last_error


def get_model_runtime() -> dict:
    if _model_runtime is None:
        return {
            "model_path": str(MODEL_PATH),
            "device": DEVICE,
            "compute_type": COMPUTE_TYPE,
        }
    return _model_runtime.copy()

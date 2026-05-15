from pathlib import Path

import json
import os
import sys

# =========================
# 项目基础路径
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = PROJECT_ROOT / "audio2text.settings.json"
EXAMPLE_SETTINGS_FILE = PROJECT_ROOT / "audio2text.settings.example.json"


def _load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}

    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"配置文件格式错误: {SETTINGS_FILE}. 请检查 JSON 语法。"
        ) from exc


SETTINGS = _load_settings()


def _setting(key: str, env_name: str, default=None, *, legacy_env_names: tuple[str, ...] = ()):
    for candidate in (env_name, *legacy_env_names):
        env_value = os.environ.get(candidate)
        if env_value not in (None, ""):
            return env_value

    value = SETTINGS.get(key)
    if value in (None, ""):
        return default
    return value


def _resolve_path(raw_value: str | Path) -> Path:
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _path_setting(key: str, env_name: str, default: Path) -> Path:
    raw_value = _setting(key, env_name, str(default))
    return _resolve_path(raw_value)


def _optional_path_setting(key: str, env_name: str) -> Path | None:
    raw_value = _setting(key, env_name, None)
    if raw_value in (None, ""):
        return None
    return _resolve_path(raw_value)


def _bool_setting(key: str, env_name: str, default: bool) -> bool:
    value = _setting(key, env_name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_setting(key: str, env_name: str, default: int) -> int:
    value = _setting(key, env_name, default)
    return int(value)


def _csv_setting(key: str, env_name: str, default: list[str]) -> list[str]:
    value = _setting(key, env_name, default)
    if isinstance(value, (list, tuple, set)):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = [item.strip() for item in str(value).split(",")]
    return [item for item in candidates if item]


APP_TITLE = "Praxis AI｜无界笃行 · 万象成文"
API_HOST = str(_setting("api_host", "AUDIO2TEXT_API_HOST", "127.0.0.1"))
API_PORT = _int_setting("api_port", "AUDIO2TEXT_API_PORT", 8000)

WORKSPACE_DIR = _path_setting(
    "workspace_dir",
    "AUDIO2TEXT_WORKSPACE_DIR",
    PROJECT_ROOT / "workspace",
)
OUTPUT_DIR = WORKSPACE_DIR / "output"
TEMP_DIR = WORKSPACE_DIR / "temp"
LOGS_DIR = WORKSPACE_DIR / "logs"
RUNTIME_DIR = WORKSPACE_DIR / "runtime"
MODELS_DIR = RUNTIME_DIR / "models"
PLAYWRIGHT_BROWSERS_DIR = RUNTIME_DIR / "playwright-browsers"

LEGACY_BUNDLED_MODEL_PATH = PROJECT_ROOT / "Assets" / "Models" / "FasterWhisper" / "medium"
DEFAULT_MODEL_PATH = MODELS_DIR / "faster-whisper" / "medium"
_configured_model_path = _optional_path_setting("model_path", "AUDIO2TEXT_MODEL_PATH")
if _configured_model_path is not None:
    MODEL_PATH = _configured_model_path
    MODEL_PATH_SOURCE = "explicit"
elif DEFAULT_MODEL_PATH.exists():
    MODEL_PATH = DEFAULT_MODEL_PATH
    MODEL_PATH_SOURCE = "workspace_default"
elif LEGACY_BUNDLED_MODEL_PATH.exists():
    MODEL_PATH = LEGACY_BUNDLED_MODEL_PATH
    MODEL_PATH_SOURCE = "legacy_fallback"
else:
    MODEL_PATH = DEFAULT_MODEL_PATH
    MODEL_PATH_SOURCE = "workspace_default"
MODEL_PATH_IS_LEGACY_FALLBACK = MODEL_PATH_SOURCE == "legacy_fallback"

_ffmpeg_binary = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
DEFAULT_FFMPEG_PATH = RUNTIME_DIR / "ffmpeg" / _ffmpeg_binary
_configured_ffmpeg_path = _optional_path_setting("ffmpeg_path", "AUDIO2TEXT_FFMPEG_PATH")
FFMPEG_PATH = _configured_ffmpeg_path or DEFAULT_FFMPEG_PATH
FFMPEG_PATH_SOURCE = "explicit" if _configured_ffmpeg_path is not None else "workspace_default"

SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".mp4", ".mov", ".mkv"}
MAX_UPLOAD_SIZE_MB = _int_setting(
    "max_upload_size_mb",
    "AUDIO2TEXT_MAX_UPLOAD_SIZE_MB",
    1024,
)

WORKER_RELAY_BASE = "https://wanxiang.praxisai.online/__proxy__"
WORKER_TRANSCRIBE_URL = "https://wanxiang.praxisai.online/__transcribe__"

_cn_proxy_raw = str(
    _setting(
        "cn_platform_proxy",
        "AUDIO2TEXT_CN_PLATFORM_PROXY",
        "",
    )
).strip()
CN_PLATFORM_PROXY = _cn_proxy_raw or None
CN_PLATFORM_RELAY_URL = CN_PLATFORM_PROXY if CN_PLATFORM_PROXY else WORKER_RELAY_BASE

DEVICE = str(_setting("device", "AUDIO2TEXT_DEVICE", "auto")).lower()
COMPUTE_TYPE = str(
    _setting(
        "compute_type",
        "AUDIO2TEXT_COMPUTE_TYPE",
        "int8" if DEVICE == "cpu" else "int8_float16",
    )
).lower()
LANGUAGE = str(_setting("language", "AUDIO2TEXT_LANGUAGE", "auto")).strip().lower()
BEAM_SIZE = _int_setting("beam_size", "AUDIO2TEXT_BEAM_SIZE", 1)
VAD_FILTER = _bool_setting("vad_filter", "AUDIO2TEXT_VAD_FILTER", True)

EXPORT_PLAIN_TEXT = _bool_setting(
    "export_plain_text",
    "AUDIO2TEXT_EXPORT_PLAIN_TEXT",
    True,
)
EXPORT_TIMESTAMP_TEXT = _bool_setting(
    "export_timestamp_text",
    "AUDIO2TEXT_EXPORT_TIMESTAMP_TEXT",
    True,
)
EXPORT_MARKDOWN = _bool_setting(
    "export_markdown",
    "AUDIO2TEXT_EXPORT_MARKDOWN",
    True,
)
EXPORT_META = _bool_setting("export_meta", "AUDIO2TEXT_EXPORT_META", True)

TRANSCRIPTION_PROVIDER = (
    str(
        _setting(
            "transcription_provider",
            "AUDIO2TEXT_TRANSCRIPTION_PROVIDER",
            "local_faster_whisper",
            legacy_env_names=("RELAY_TRANSCRIPTION_PROVIDER",),
        )
    )
    .strip()
    .lower()
    or "local_faster_whisper"
)

DEPLOYMENT_MODE = str(
    _setting("deployment_mode", "AUDIO2TEXT_DEPLOYMENT_MODE", "local")
).strip().lower() or "local"
ALLOW_DEGRADED_START = _bool_setting(
    "allow_degraded_start",
    "AUDIO2TEXT_ALLOW_DEGRADED_START",
    False,
)
PUBLIC_PREVIEW_MODE = _bool_setting(
    "public_preview_mode",
    "AUDIO2TEXT_PUBLIC_PREVIEW_MODE",
    DEPLOYMENT_MODE in {"cloud_preview", "public_preview"},
)
OPENAI_COMPATIBLE_BASE_URL = str(
    _setting(
        "openai_compatible_base_url",
        "AUDIO2TEXT_OPENAI_COMPATIBLE_BASE_URL",
        "",
        legacy_env_names=("RELAY_OPENAI_COMPATIBLE_BASE_URL",),
    )
).strip()
OPENAI_COMPATIBLE_API_KEY = str(
    _setting(
        "openai_compatible_api_key",
        "AUDIO2TEXT_OPENAI_COMPATIBLE_API_KEY",
        "",
        legacy_env_names=("RELAY_OPENAI_COMPATIBLE_API_KEY",),
    )
).strip()
OPENAI_COMPATIBLE_MODEL = (
    str(
        _setting(
            "openai_compatible_model",
            "AUDIO2TEXT_OPENAI_COMPATIBLE_MODEL",
            "gpt-4o-mini-transcribe",
            legacy_env_names=("RELAY_OPENAI_COMPATIBLE_MODEL",),
        )
    )
    .strip()
    or "gpt-4o-mini-transcribe"
)

ALLOWED_ORIGINS = _csv_setting(
    "allowed_origins",
    "AUDIO2TEXT_ALLOWED_ORIGINS",
    [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
)

for folder in [
    WORKSPACE_DIR,
    OUTPUT_DIR,
    TEMP_DIR,
    LOGS_DIR,
    RUNTIME_DIR,
    MODELS_DIR,
    DEFAULT_FFMPEG_PATH.parent,
    PLAYWRIGHT_BROWSERS_DIR,
]:
    folder.mkdir(parents=True, exist_ok=True)

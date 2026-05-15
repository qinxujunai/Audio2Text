from __future__ import annotations

import os

from scripts.config import (
    ALLOWED_ORIGINS,
    ALLOW_DEGRADED_START,
    API_HOST,
    API_PORT,
    APP_TITLE,
    BEAM_SIZE,
    CN_PLATFORM_PROXY,
    CN_PLATFORM_RELAY_URL,
    COMPUTE_TYPE,
    DEPLOYMENT_MODE,
    DEVICE,
    FFMPEG_PATH,
    FFMPEG_PATH_SOURCE,
    LANGUAGE,
    LEGACY_BUNDLED_MODEL_PATH,
    LOGS_DIR,
    MAX_UPLOAD_SIZE_MB,
    MODEL_PATH,
    MODEL_PATH_IS_LEGACY_FALLBACK,
    MODEL_PATH_SOURCE,
    OPENAI_COMPATIBLE_API_KEY,
    OPENAI_COMPATIBLE_BASE_URL,
    OPENAI_COMPATIBLE_MODEL,
    PLAYWRIGHT_BROWSERS_DIR,
    PROJECT_ROOT,
    PUBLIC_PREVIEW_MODE,
    RUNTIME_DIR,
    SUPPORTED_EXTENSIONS,
    TEMP_DIR,
    TRANSCRIPTION_PROVIDER,
    VAD_FILTER,
    WORKSPACE_DIR,
    WORKER_RELAY_BASE,
    WORKER_TRANSCRIBE_URL,
)


PRODUCT_NAME = os.environ.get("RELAY_PRODUCT_NAME", "Praxis AI\uFF5C\u65E0\u754C\u7B03\u884C")
PRODUCT_FEATURE_NAME = os.environ.get("RELAY_PRODUCT_FEATURE_NAME", "\u4E07\u8C61\u6210\u6587")
PRODUCT_SUMMARY = (
    "\u516C\u5F00\u5185\u5BB9\u5165\u9875\uFF0C\u7247\u523B\u4E4B\u540E\uFF0C\u81EA\u4F1A\u843D\u5B57\u6210\u6587\u3002"
)
PRODUCT_SLOGAN = "\u4E07\u8C61\u5165\u9875\uFF0C\u843D\u5B57\u6210\u6587\u3002"

APP_DISPLAY_NAME = f"{PRODUCT_NAME} API"
CODE_NAME = os.environ.get("RELAY_ENGINE_NAME", "capture_text")

FRONTEND_DIR = PROJECT_ROOT / "frontend"
WEB_APP_DIR = PROJECT_ROOT / "web"
WEB_DIST_DIR = FRONTEND_DIR / "dist"
CAPTURES_DIR = WORKSPACE_DIR / "captures"
ARTIFACTS_DIR = WORKSPACE_DIR / "artifacts"
CACHE_DIR = WORKSPACE_DIR / "cache"
BROWSER_PROFILE_DIR = RUNTIME_DIR / "browser-profile"

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_BROWSERS_DIR))

for folder in [
    WORKSPACE_DIR,
    CAPTURES_DIR,
    ARTIFACTS_DIR,
    CACHE_DIR,
    LOGS_DIR,
    TEMP_DIR,
    RUNTIME_DIR,
    BROWSER_PROFILE_DIR,
    PLAYWRIGHT_BROWSERS_DIR,
]:
    folder.mkdir(parents=True, exist_ok=True)


URL_INPUT_PLATFORM_HINTS = {
    "youtube": "YouTube",
    "bilibili": "\u54D4\u54E9\u54D4\u54E9",
    "xiaoyuzhou": "\u5C0F\u5B87\u5B99",
    "douyin": "\u6296\u97F3",
    "xiaohongshu": "\u5C0F\u7EA2\u4E66",
    "wechat_article": "\u5FAE\u4FE1\u516C\u4F17\u53F7",
    "generic_web": "\u7F51\u9875\u6587\u7AE0",
}

STORE_MODE = "file"
DEFAULT_TIMEOUT_SECONDS = 30.0
CAPTURE_HISTORY_LIMIT = int(os.environ.get("CAPTURE_HISTORY_LIMIT", "12"))
FREE_DURATION_MINUTES = int(os.environ.get("FREE_DURATION_MINUTES", "30"))
DAILY_CAPTURE_LIMIT = int(os.environ.get("DAILY_CAPTURE_LIMIT", "12"))
MAX_VIDEO_DURATION_MINUTES = int(os.environ.get("MAX_VIDEO_DURATION_MINUTES", "30"))
MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "200"))
ADMIN_IPS = {ip.strip() for ip in os.environ.get("ADMIN_IPS", "").split(",") if ip.strip()}
FREE_DURATION_SECONDS = FREE_DURATION_MINUTES * 60
TRANSCRIPTION_AVAILABLE = (
    DEPLOYMENT_MODE == "cloud_preview"
    or (
        TRANSCRIPTION_PROVIDER == "openai_compatible"
        and bool(OPENAI_COMPATIBLE_BASE_URL)
        and bool(OPENAI_COMPATIBLE_API_KEY)
        and bool(OPENAI_COMPATIBLE_MODEL)
    )
    or (
        TRANSCRIPTION_PROVIDER != "openai_compatible"
        and MODEL_PATH.exists()
        and MODEL_PATH.is_dir()
    )
)

RELAY_RUN_MODE = os.environ.get("RELAY_RUN_MODE", "local").strip().lower() or "local"
RELAY_AUTO_START_WORKER = os.environ.get("RELAY_AUTO_START_WORKER", "1").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
)

def read_cookie_payload(raw_text: str | None) -> str:
    if not raw_text:
        return ""
    return raw_text.strip()


def runtime_summary() -> dict:
    return {
        "app_title": APP_TITLE,
        "display_name": APP_DISPLAY_NAME,
        "product_name": PRODUCT_NAME,
        "product_feature_name": PRODUCT_FEATURE_NAME,
        "product_summary": PRODUCT_SUMMARY,
        "product_slogan": PRODUCT_SLOGAN,
        "api_base": f"http://{API_HOST}:{API_PORT}",
        "workspace_dir": str(WORKSPACE_DIR),
        "captures_dir": str(CAPTURES_DIR),
        "artifacts_dir": str(ARTIFACTS_DIR),
        "store_mode": STORE_MODE,
        "run_mode": RELAY_RUN_MODE,
        "deployment_mode": DEPLOYMENT_MODE,
        "public_preview_mode": PUBLIC_PREVIEW_MODE,
        "allow_degraded_start": ALLOW_DEGRADED_START,
        "auto_start_worker": RELAY_AUTO_START_WORKER,
        "transcription_provider": TRANSCRIPTION_PROVIDER,
        "transcription_available": TRANSCRIPTION_AVAILABLE,
        "model_path": str(MODEL_PATH),
        "model_path_source": MODEL_PATH_SOURCE,
        "model_path_legacy_fallback": MODEL_PATH_IS_LEGACY_FALLBACK,
        "legacy_model_path": str(LEGACY_BUNDLED_MODEL_PATH),
        "ffmpeg_path": str(FFMPEG_PATH),
        "ffmpeg_path_source": FFMPEG_PATH_SOURCE,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "language": LANGUAGE,
        "beam_size": BEAM_SIZE,
        "vad_filter": VAD_FILTER,
        "max_upload_size_mb": MAX_UPLOAD_SIZE_MB,
        "capture_history_limit": CAPTURE_HISTORY_LIMIT,
        "free_duration_minutes": FREE_DURATION_MINUTES,
        "cn_platform_proxy": bool(CN_PLATFORM_PROXY),
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "supported_platforms": URL_INPUT_PLATFORM_HINTS,
        "allowed_origins": ALLOWED_ORIGINS,
        "playwright_browsers_dir": str(PLAYWRIGHT_BROWSERS_DIR),
        "browser_profile_dir": str(BROWSER_PROFILE_DIR),
    }

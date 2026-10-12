from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.store_fs import CaptureStoreUnavailableError
from app.pipeline import (
    enqueue_capture,
    ensure_capture_cover,
    ensure_worker_started,
    recover_pending_captures,
    repair_legacy_audio_artifact,
    retry_capture_image,
)
from app.resolver import infer_source_item_id, resolve_url
from app.runtime_packs import RuntimePackError, RuntimePackManager
from app.runtime_preflight import browser_runtime_available
from app.browser_provider import (
    browser_runtime_status,
    refresh_browser_runtime,
    shutdown_browser_runtime,
)
from app.providers.transcription import transcription_runtime_status
from app.schemas import (
    ArtifactPayloadModel,
    BrowserSessionRequest,
    CaptureCreateUrlRequest,
    CaptureEnvelopeModel,
    CaptureEventEnvelopeModel,
    CaptureListItemModel,
    CaptureListResponse,
    CaptureQualityPayloadModel,
    CaptureResultPayloadModel,
    CaptureSourceImagePayloadModel,
    CaptureSourcePayloadModel,
    CaptureStatePayloadModel,
    ContentFactItemModel,
    ResultViewsModel,
    SourceMetaModel,
)
from app.settings import (
    ADMIN_IPS,
    ALLOWED_ORIGINS,
    APP_COMMIT,
    APP_DISPLAY_NAME,
    APP_VERSION,
    CAPTURE_HISTORY_LIMIT,
    DAILY_CAPTURE_LIMIT,
    DESKTOP_TOKEN,
    FREE_DURATION_MINUTES,
    FRONTEND_DIR,
    MAX_UPLOAD_SIZE_MB,
    MAX_VIDEO_DURATION_MINUTES,
    PROJECT_ROOT,
    PRODUCT_FEATURE_NAME,
    PRODUCT_NAME,
    PRODUCT_SLOGAN,
    PRODUCT_SUMMARY,
    DEPLOYMENT_MODE,
    FFMPEG_PATH,
    FRONTEND_BUILD_MANIFEST,
    MODEL_PATH,
    PLAYWRIGHT_BROWSERS_DIR,
    PUBLIC_PREVIEW_MODE,
    RUNTIME_DIR,
    RUNTIME_PACK_MANIFEST,
    RUNTIME_TARGET,
    SUPPORTED_EXTENSIONS,
    TEMP_DIR,
    TRANSCRIPTION_AVAILABLE,
    TRANSCRIPTION_PROVIDER,
    URL_INPUT_PLATFORM_HINTS,
    WEB_DIST_DIR,
)
from app.storage import get_capture_repository
from scripts.logger import get_logger

logger = get_logger("capture_api", "capture_api.log")
repository = get_capture_repository()
runtime_pack_manager = RuntimePackManager(RUNTIME_PACK_MANIFEST, RUNTIME_DIR)
UPLOAD_CHUNK_SIZE = 1024 * 1024

app = FastAPI(title=APP_DISPLAY_NAME)
from app.local_tools_api import router as local_tools_router
app.include_router(local_tools_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_INTERNAL_ERROR_MESSAGE = "服务暂时繁忙，请稍后重试。"
DESKTOP_ALLOWED_ORIGINS = {
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
}
_browser_session_process: subprocess.Popen | None = None
_BROWSER_SESSION_STATE_FILE = RUNTIME_DIR / "browser-session-state.json"
LEGACY_PERMANENT_REASON_CODES = frozenset(
    {
        "source_not_found",
        "source_access_restricted",
        "youtube_video_unavailable",
        "bilibili_video_invalid",
        "xiaohongshu_placeholder_page",
        "xiaoyuzhou_audio_missing",
        "image_assets_missing",
        "media_or_subtitle_missing",
        "text_or_images_missing",
    }
)


def _browser_session_status() -> dict[str, Any]:
    """Expose only the lifecycle state of the user-led browser helper."""
    global _browser_session_process
    process = _browser_session_process
    if process is not None:
        exit_code = process.poll()
        if exit_code is None:
            return {"state": "open"}
        _browser_session_process = None
        _BROWSER_SESSION_STATE_FILE.unlink(missing_ok=True)
        return {"state": "exited", "exit_code": int(exit_code)}

    # The API process can restart while the user-led helper is still open.
    # Keep only a local PID marker so a second profile-locking browser is not
    # started; no URL, cookie, or account data is persisted here.
    try:
        payload = json.loads(_BROWSER_SESSION_STATE_FILE.read_text(encoding="utf-8"))
        pid = int(payload.get("pid", 0)) if isinstance(payload, dict) else 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pid = 0
    if pid > 0:
        try:
            os.kill(pid, 0)
        except OSError:
            _BROWSER_SESSION_STATE_FILE.unlink(missing_ok=True)
        else:
            return {"state": "open"}
    return {"state": "idle"}


def _validated_browser_session_url(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > 4096:
        raise HTTPException(status_code=400, detail="平台链接过长，暂时无法打开。")
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = (
        host == "xiaohongshu.com"
        or host.endswith(".xiaohongshu.com")
        or host == "douyin.com"
        or host.endswith(".douyin.com")
    )
    if parsed.scheme != "https" or not allowed:
        raise HTTPException(status_code=400, detail="这里只能打开受支持平台的安全链接。")
    return cleaned


@app.middleware("http")
async def enforce_desktop_loopback_boundary(request: Request, call_next):
    if RUNTIME_TARGET != "windows_desktop" or not DESKTOP_TOKEN:
        return await call_next(request)

    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse(status_code=403, content={"detail": "本地服务仅允许本机访问。"})

    origin = request.headers.get("origin", "")
    if origin and origin not in DESKTOP_ALLOWED_ORIGINS:
        return JSONResponse(status_code=403, content={"detail": "请求来源未获授权。"})

    # Browsers send the CORS preflight before they are allowed to attach the
    # desktop token header. The origin and loopback checks still apply here;
    # every actual API request remains token-protected below.
    if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
        return await call_next(request)

    supplied_token = request.headers.get("x-wanxiang-desktop-token", "")
    if not supplied_token:
        supplied_token = request.query_params.get("desktop_token", "")
    if not hmac.compare_digest(supplied_token, DESKTOP_TOKEN):
        return JSONResponse(status_code=401, content={"detail": "本地服务授权已失效，请重新打开应用。"})

    return await call_next(request)


def _is_windows_connection_reset_noise(context: dict[str, Any]) -> bool:
    if not sys.platform.startswith("win"):
        return False
    exception = context.get("exception")
    if not isinstance(exception, ConnectionResetError):
        return False
    if getattr(exception, "winerror", None) != 10054 and getattr(exception, "errno", None) != 10054:
        return False

    handle = str(context.get("handle", ""))
    message = str(context.get("message", ""))
    return "_ProactorBasePipeTransport._call_connection_lost" in handle or "_call_connection_lost" in message


def _install_asyncio_connection_reset_filter() -> None:
    if not sys.platform.startswith("win"):
        return

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handle_exception(event_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _is_windows_connection_reset_noise(context):
            return
        if previous_handler is not None:
            previous_handler(event_loop, context)
            return
        event_loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)


def _dist_available() -> bool:
    return _frontend_build_status()["status"] == "ready"


def _frontend_build_status() -> dict[str, Any]:
    if not WEB_DIST_DIR.exists() or not (WEB_DIST_DIR / "index.html").is_file():
        return {"status": "missing", "build_id": None, "git_sha": None}
    if not FRONTEND_BUILD_MANIFEST.is_file():
        return {"status": "manifest_missing", "build_id": None, "git_sha": None}
    try:
        payload = json.loads(FRONTEND_BUILD_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "manifest_invalid", "build_id": None, "git_sha": None}
    if payload.get("schema_version") != 1 or not payload.get("build_id"):
        return {"status": "manifest_invalid", "build_id": None, "git_sha": None}
    output_files = payload.get("output_files")
    if not isinstance(output_files, list) or not output_files or not all(
        isinstance(item, str)
        and item
        and not Path(item).is_absolute()
        and ".." not in Path(item).parts
        for item in output_files
    ):
        return {"status": "manifest_invalid", "build_id": None, "git_sha": None}
    missing_outputs = [item for item in output_files if not (WEB_DIST_DIR / item).is_file()]
    if missing_outputs:
        return {
            "status": "outputs_missing",
            "build_id": str(payload.get("build_id")),
            "git_sha": str(payload.get("git_sha") or "unknown"),
        }
    return {
        "status": "ready",
        "build_id": str(payload.get("build_id")),
        "git_sha": str(payload.get("git_sha") or "unknown"),
    }


if _dist_available():
    assets_dir = WEB_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="capture-assets")


@app.middleware("http")
async def set_frontend_asset_cache_policy(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path or "/"
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path in {"/", "/health", "/config"} or path.startswith("/c/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _normalized_result(result):
    if result is None:
        return None
    if result.views.primary or result.views.transcript or result.views.article:
        return result
    return result.model_copy(
        update={
            "views": ResultViewsModel(
                primary=result.primary_text,
                transcript=result.transcript_text,
                subtitle=result.subtitle_text,
                article=result.article_text,
                notes=result.notes_text,
                timeline=result.timeline_text,
                markdown=result.markdown_text,
                json_view=result.json_text,
                meta=result.meta_text,
                ocr=result.ocr_text,
            )
        }
    )


def _display_result(capture, result):
    if result is None:
        return None
    platform = getattr(capture, "source_platform", "") or getattr(getattr(capture, "source", None), "platform", "")
    if platform != "xiaoyuzhou":
        return result
    if result.text_source == "transcript":
        return result
    return result.model_copy(
        update={
            "primary_text": "",
            "primary_result_type": "",
            "text_source": "none",
            "notes_text": "",
            "article_text": "",
            "markdown_text": "",
            "segments": [],
            "views": ResultViewsModel(),
            "artifacts": [],
        }
    )


def _display_source_description(capture, result) -> str | None:
    platform = getattr(capture, "source_platform", "") or getattr(getattr(capture, "source", None), "platform", "")
    if platform == "xiaoyuzhou" and result is not None and result.text_source != "transcript":
        return ""
    return capture.source.description


def _fallback_content_facts(capture, source, result) -> list[ContentFactItemModel]:
    facts: list[ContentFactItemModel] = []

    def append(key: str, label: str, value: str | None, href: str | None = None) -> None:
        cleaned = (value or "").strip()
        if not cleaned:
            return
        facts.append(ContentFactItemModel(key=key, label=label, value=cleaned, href=href))

    append("title", "标题", capture.title or source.canonical_url)
    append("platform", "平台", source.platform)
    append("content_type", "内容类型", source.content_type)
    append("author", "作者", source.author)
    if source.duration_seconds:
        append("duration", "时长", f"{round(source.duration_seconds)} 秒")
    if source.image_urls:
        append("image_count", "图片数量", str(len(source.image_urls)))
    return facts


def _public_artifact(artifact) -> ArtifactPayloadModel:
    status = getattr(artifact, "status", "ready")
    preview_url = getattr(artifact, "preview_url", None)
    if status == "ready" and not preview_url:
        preview_url = f"{artifact.download_url}/preview"
    return ArtifactPayloadModel(
        type=artifact.type,
        label=artifact.label,
        download_url=artifact.download_url,
        preview_url=preview_url,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        status=status,
        optional=bool(getattr(artifact, "optional", False)),
    )


def _public_capture_status(capture) -> str:
    """Project legacy whole-capture failures into the actionable public state.

    ``failed`` remains readable internally for old files and artifact-level
    compatibility, but it is not a user-facing whole-capture state. Keeping
    this projection separate from the mutating legacy repair gives every
    response builder a final defense-in-depth boundary.
    """
    return "needs_user_action" if capture.status == "failed" else capture.status


def _result_state(capture, result) -> str:
    public_status = _public_capture_status(capture)
    if public_status == "needs_user_action":
        return "awaiting_user"
    has_text = bool(result and (result.views.primary or result.primary_text).strip())
    declared_state = str(getattr(capture, "result_state", "processing") or "processing")
    if declared_state == "text_ready" and has_text:
        return "text_ready"
    if public_status == "done" and bool(getattr(capture, "asset_preparation_pending", False)) and has_text:
        return "text_ready"
    if public_status == "done":
        return "complete"
    return "processing"


def _normalize_legacy_user_action_capture(capture):
    """Expose all pre-migration terminal captures as actionable states.

    Older records were persisted as ``failed`` before the product used a calm,
    actionable terminal surface. Normalize them at every API boundary so the
    web UI, desktop client, history and SSE consumers agree on the state
    without rewriting the user's local record in the background.
    """
    reason_code = str(getattr(capture.processing, "failure_reason_code", "") or "")
    if capture.status != "failed":
        return capture
    capture.status = "needs_user_action"
    capture.result_state = "awaiting_user"
    capture.processing.current_stage = "awaiting_user"
    capture.processing.progress_percent = max(18, capture.processing.progress_percent or 0)
    if not reason_code:
        capture.processing.failure_reason_code = "internal_error"
    if reason_code not in LEGACY_PERMANENT_REASON_CODES:
        capture.processing.retryable = True
    return capture


def _public_source_images(source, result=None) -> list[CaptureSourceImagePayloadModel]:
    image_urls = list(source.image_urls or [])
    live_urls = list(getattr(source, "live_photo_video_urls", []) or [])
    artifacts = list(getattr(result, "artifacts", []) or [])
    image_artifacts = {
        int(item.type.removeprefix("image_")): item
        for item in artifacts
        if re.fullmatch(r"image_\d+", item.type)
    }
    items: list[CaptureSourceImagePayloadModel] = []
    for index, image_url in enumerate(image_urls, start=1):
        cleaned_image_url = (image_url or "").strip()
        if not cleaned_image_url:
            continue
        live_url = (live_urls[index - 1] if index - 1 < len(live_urls) else "").strip() or None
        artifact = image_artifacts.get(index)
        status = getattr(artifact, "status", "pending") if artifact is not None else "pending"
        preview_url = None
        download_url = None
        width = None
        height = None
        thumbnail_url = None
        if artifact is not None and status == "ready":
            download_url = artifact.download_url
            preview_url = getattr(artifact, "preview_url", None) or f"{artifact.download_url}/preview"
            width = getattr(artifact, "width", None)
            height = getattr(artifact, "height", None)
            thumbnail_path_value = getattr(artifact, "thumbnail_path", None)
            if thumbnail_path_value:
                thumbnail_path = Path(thumbnail_path_value)
                if thumbnail_path.is_file():
                    stat = thumbnail_path.stat()
                    version = f"{stat.st_mtime_ns:x}{stat.st_size:x}"
                    capture_base = str(artifact.download_url).rsplit("/artifacts/", 1)[0]
                    thumbnail_url = f"{capture_base}/images/{index}/thumbnail?v={version}"
        items.append(
            CaptureSourceImagePayloadModel(
                index=index,
                image_url=cleaned_image_url,
                live_photo_video_url=live_url,
                delivery_status=status,
                preview_url=preview_url,
                download_url=download_url,
                width=width,
                height=height,
                thumbnail_url=thumbnail_url,
            )
        )
    return items


_SOURCE_WARNING_TECHNICAL_PATTERNS = re.compile(
    r"(?:traceback|exception|filenotfound|keyerror|yt[- ]?dlp|no video formats|"
    r"http error|error:\s*\[|file [\"']|[a-z]:\\|https?://|\\\\)",
    re.IGNORECASE,
)


def _public_capture_error_message(capture) -> str | None:
    """Keep raw extraction failures out of every public capture envelope.

    The worker keeps detailed diagnostics locally, but the web and desktop
    clients should receive a calm, actionable sentence.  This boundary also
    protects clients that do not run the React copy normalizer.
    """

    cleaned = " ".join(str(getattr(capture, "error_message", "") or "").split()).strip()
    if not cleaned:
        return None
    if _SOURCE_WARNING_TECHNICAL_PATTERNS.search(cleaned) or len(cleaned) > 240:
        return "处理暂时中断，已经自动尝试恢复。你可以重新处理这条内容。"
    return (
        cleaned
        .replace("处理失败", "处理暂未完成")
        .replace("操作失败", "操作暂未完成")
        .replace("上传失败", "上传暂未完成")
        .replace("下载失败", "下载暂未完成")
        .replace("失败", "暂未完成")
        .replace("出错", "暂时中断")
    )


def _public_source_warnings(source) -> list[str]:
    """Expose only short, user-safe source warnings through the API envelope."""

    public: list[str] = []
    for warning in list(getattr(source, "warnings", []) or []):
        cleaned = " ".join(str(warning or "").split()).strip()
        if not cleaned:
            continue
        if _SOURCE_WARNING_TECHNICAL_PATTERNS.search(cleaned) or len(cleaned) > 240:
            cleaned = "部分提取路径暂未就绪，已自动切换备用路径。"
        cleaned = cleaned.replace("失败", "暂未就绪")
        if cleaned not in public:
            public.append(cleaned)
    return public[:6]


def _preview_without_title(title: str | None, text: str | None, *, limit: int = 160) -> str:
    cleaned_title = " ".join((title or "").split()).strip().rstrip("…")
    cleaned_text = " ".join((text or "").split()).strip()
    if not cleaned_text:
        return ""
    if cleaned_title and cleaned_text.startswith(cleaned_title):
        cleaned_text = cleaned_text[len(cleaned_title) :].lstrip(" \n:-|：，,。.!?！？")
    return cleaned_text[:limit]


def _to_capture_envelope(capture) -> CaptureEnvelopeModel:
    _normalize_legacy_user_action_capture(capture)
    public_status = _public_capture_status(capture)
    result = _display_result(capture, _normalized_result(capture.result))
    source = capture.source
    content_facts = (
        result.content_facts
        if result and getattr(result, "content_facts", None)
        else (_fallback_content_facts(capture, source, result) if result else [])
    )

    return CaptureEnvelopeModel(
        capture=CaptureStatePayloadModel(
            id=capture.id,
            status=public_status,
            result_state=_result_state(capture, result),
            input_kind=capture.input_type,
            current_stage=capture.processing.current_stage,
            progress_percent=capture.processing.progress_percent,
            progress_detail=capture.processing.progress_detail,
            title=capture.title,
            created_at=capture.created_at,
            updated_at=capture.updated_at,
            started_at=capture.started_at,
            finished_at=capture.finished_at,
            error_stage=capture.error_stage,
            error_message=_public_capture_error_message(capture),
            retry_count=capture.processing.retry_count,
            retryable=capture.processing.retryable,
            failure_reason_code=getattr(capture.processing, "failure_reason_code", None),
            asset_preparation_pending=bool(getattr(capture, "asset_preparation_pending", False)),
        ),
        source=CaptureSourcePayloadModel(
            platform=source.platform,
            media_kind=source.content_type,
            canonical_url=source.canonical_url,
            title=capture.title,
            author=source.author,
            duration_seconds=source.duration_seconds,
            thumbnail_url=source.thumbnail_url,
            description=_display_source_description(capture, result),
            detected_urls=source.detected_urls,
            image_urls=source.image_urls,
            images=_public_source_images(source, result),
            warnings=_public_source_warnings(source),
            extractor_used=source.extractor_used,
            fallback_used=source.fallback_used,
        ),
        quality=CaptureQualityPayloadModel(
            primary_result_type=result.primary_result_type if result else "",
            confidence=result.confidence if result else "low",
            completeness=result.completeness if result else "partial",
            cache_hit=result.cache_hit if result else False,
            transcript_status=result.transcript_status if result else "skipped",
            text_source=result.text_source if result else "none",
            subtitle_source=result.subtitle_source if result else "none",
            selected_language=result.selected_language if result else "",
            result_notice=result.result_notice if result else "",
        ),
        result=CaptureResultPayloadModel(
            primary_text=result.primary_text if result else "",
            extraction_path=result.extraction_path if result else [],
            segments=result.segments if result else [],
            views=result.views if result else ResultViewsModel(),
            content_facts=content_facts,
        ),
        artifacts=[_public_artifact(item) for item in (result.artifacts if result else [])],
    )


def _transition_event_names(
    capture,
    envelope: CaptureEnvelopeModel,
    *,
    previous_result_state: str | None,
    ready_artifact_types: set[str],
) -> tuple[list[str], set[str]]:
    events: list[str] = []
    current_state = envelope.capture.result_state
    if (
        current_state in {"text_ready", "complete"}
        and previous_result_state not in {"text_ready", "complete"}
        and bool(envelope.result.primary_text.strip())
    ):
        events.append("text_ready")

    current_ready = {
        artifact.type
        for artifact in envelope.artifacts
        if artifact.status == "ready"
    }
    if current_ready - ready_artifact_types:
        events.append("artifact_ready")

    reason_code = str(getattr(capture.processing, "failure_reason_code", "") or "")
    if reason_code.startswith("runtime_"):
        events.append("runtime_required")
    return events, current_ready


def _public_capture(capture) -> CaptureListItemModel:
    public_status = _public_capture_status(capture)
    result = _display_result(capture, _normalized_result(capture.result))
    platform = getattr(capture, "source_platform", "") or getattr(getattr(capture, "source", None), "platform", "")
    preview = ""
    if result and result.views.primary:
        preview = _preview_without_title(capture.title, result.views.primary)
    elif _display_source_description(capture, result):
        preview = _preview_without_title(capture.title, _display_source_description(capture, result))
    elif capture.title and not (platform == "xiaoyuzhou" and result is not None and result.text_source == "none"):
        preview = capture.title[:160]
    return CaptureListItemModel(
        id=capture.id,
        status=public_status,
        title=capture.title,
        source_platform=capture.source_platform,
        content_type=capture.content_type,
        primary_result_type=result.primary_result_type if result else None,
        created_at=capture.created_at,
        updated_at=capture.updated_at,
        preview_text=preview,
    )


def _should_expose_capture_in_recent(capture) -> bool:
    if capture.status in {"failed", "needs_user_action"}:
        return False

    title = (capture.title or "").strip().lower()
    description = (capture.source.description or "").strip().lower()
    if capture.source_platform == "xiaohongshu" and (
        "xhslink.com" in title or "xhslink.com" in description
    ):
        return False
    return True


def _selected_transcription_available() -> bool:
    if DEPLOYMENT_MODE != "local" or TRANSCRIPTION_PROVIDER == "openai_compatible":
        return TRANSCRIPTION_AVAILABLE
    from app.local_preferences import transcription_options
    from scripts.config import COMPUTE_TYPE, DEVICE, MODEL_PATH
    options = transcription_options(device=DEVICE, model_path=MODEL_PATH, compute_type=COMPUTE_TYPE,
                                    desktop=RUNTIME_TARGET == "windows_desktop")
    if options["engine"] != "sensevoice":
        return TRANSCRIPTION_AVAILABLE
    directory = options["model_path"]
    gpu = options["execution_device"] == "cuda"
    ready = (directory / ("model.onnx" if gpu else "model.int8.onnx")).is_file() and (directory / "tokens.txt").is_file()
    return ready and (not gpu or (options["sdk_path"] / "sherpa_onnx/lib/onnxruntime_providers_cuda.dll").is_file())


def _config_payload() -> dict[str, Any]:
    return {
        "product_name": PRODUCT_NAME,
        "product_feature_name": PRODUCT_FEATURE_NAME,
        "product_summary": PRODUCT_SUMMARY,
        "product_slogan": PRODUCT_SLOGAN,
        "url_input_platform_hints": URL_INPUT_PLATFORM_HINTS,
        "capture_history_limit": CAPTURE_HISTORY_LIMIT,
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_size_mb": MAX_UPLOAD_SIZE_MB,
        "free_duration_minutes": FREE_DURATION_MINUTES,
        "deployment_mode": DEPLOYMENT_MODE,
        "public_preview_mode": PUBLIC_PREVIEW_MODE,
        "transcription_available": _selected_transcription_available(),
        "transcription_provider": TRANSCRIPTION_PROVIDER,
        "runtime_target": RUNTIME_TARGET,
        "capabilities": {
            "url_capture": True,
            "file_upload": True,
            "source_audio": True,
            "hardware_auto_selection": True,
            "desktop_runtime_management": RUNTIME_TARGET == "windows_desktop",
            "cloud_demo": DEPLOYMENT_MODE == "cloud_preview",
        },
    }


def _component_status() -> dict[str, str]:
    local_runtime = RUNTIME_TARGET in {"local_web", "windows_desktop"}
    transcription_ready = _selected_transcription_available()
    return {
        "api": "ready",
        "frontend": "embedded" if RUNTIME_TARGET == "windows_desktop" else ("ready" if _dist_available() else "missing"),
        "storage": "ready" if repository is not None else "unavailable",
        "transcription": "ready" if transcription_ready else "unavailable",
        "ffmpeg": "ready" if FFMPEG_PATH.exists() else ("missing" if local_runtime else "not_required"),
        "browser": "ready" if browser_runtime_available() else ("missing" if local_runtime else "not_required"),
        "model": "ready" if local_runtime and transcription_ready else ("missing" if local_runtime else "not_required"),
    }


def _ensure_upload_extension(file_name: str) -> str:
    cleaned_name = Path(file_name or "upload").name or "upload"
    extension = Path(cleaned_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{extension or 'unknown'}。支持：{supported}")
    return cleaned_name


def _ensure_upload_size(size_bytes: int) -> None:
    if size_bytes > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"文件过大，当前上限为 {MAX_UPLOAD_SIZE_MB} MB。")


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


async def _stream_upload_to_temp(file: UploadFile) -> tuple[str, Path]:
    file_name = _ensure_upload_extension(file.filename or "upload")
    temp_path = TEMP_DIR / f"{uuid.uuid4().hex}_{file_name}"
    size_bytes = 0

    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size_bytes += len(chunk)
                _ensure_upload_size(size_bytes)
                handle.write(chunk)
    except HTTPException:
        _safe_unlink(temp_path)
        raise
    except Exception:
        _safe_unlink(temp_path)
        raise
    finally:
        await file.close()

    return file_name, temp_path


def _cache_lookup_by_resolved_input(resolved) -> Any | None:
    if resolved.platform != "generic_web" and resolved.normalized_url:
        cached = repository.find_by_url(resolved.normalized_url)
        if cached and cached.source_platform == resolved.platform and _capture_is_reusable(cached):
            return cached
    return None


def _cache_lookup_by_identity(source: SourceMetaModel) -> Any | None:
    if not source.source_item_id or source.platform in {"generic_web", "local_file"}:
        return None
    cached = repository.find_by_source_identity(source.platform, source.source_item_id)
    if cached and _capture_is_reusable(cached):
        return cached
    return None


def _xiaoyuzhou_result_is_reusable(capture, result) -> bool:
    if (capture.source_platform or "") != "xiaoyuzhou":
        return True
    if result.text_source != "transcript":
        return False
    if not (result.views.primary or "").strip():
        return False
    stale_paths = {"page_notes", "shownotes", "description", "jsonld_description"}
    if any(step in stale_paths for step in (result.extraction_path or [])):
        return False
    return True


def _capture_is_reusable(capture) -> bool:
    if capture.status in {"queued", "processing"}:
        return True
    if capture.status != "done" or capture.result is None:
        return False
    result = _normalized_result(capture.result)
    if result is None:
        return False
    if not _xiaoyuzhou_result_is_reusable(capture, result):
        return False
    if (result.views.primary or "").strip():
        return True
    return bool(capture.source.image_urls)


async def _parse_request_payload(request: Request) -> CaptureCreateUrlRequest:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="请求内容格式不正确，请检查输入后再试。") from exc
        return CaptureCreateUrlRequest.model_validate(payload)
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        return CaptureCreateUrlRequest(url=form.get("url"), text=form.get("text"))
    raise HTTPException(status_code=400, detail="请求格式错误，请提交 JSON 文本输入或 multipart 文件。")


def _queue_if_needed(capture_id: str) -> None:
    capture = repository.get(capture_id)
    if capture is None or capture.status != "queued":
        return
    enqueue_capture(capture_id)


def _revive_capture_if_needed(capture) -> None:
    if capture is None:
        return
    if capture.status == "queued":
        _queue_if_needed(capture.id)


def _is_api_request(request: Request) -> bool:
    path = request.url.path or ""
    return path.startswith("/v1/") or path in {"/config", "/health"}


def _extract_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    return None


def _check_daily_rate_limit(client_ip: str | None, platform: str = "") -> None:
    if not client_ip or client_ip in ADMIN_IPS:
        return
    # Xiaohongshu image articles don't trigger transcription — free to use
    if platform == "xiaohongshu":
        return
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    for capture in repository.list(limit=max(DAILY_CAPTURE_LIMIT * 3, 100)):
        if capture.client_ip != client_ip:
            continue
        if capture.created_at and capture.created_at.startswith(today):
            count += 1
    if count >= DAILY_CAPTURE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"今日使用次数已达上限（{DAILY_CAPTURE_LIMIT}次），请明天再试。",
        )


@app.exception_handler(CaptureStoreUnavailableError)
async def handle_capture_store_unavailable(request: Request, exc: CaptureStoreUnavailableError):
    logger.warning("Capture storage temporarily unavailable: %s %s | %s", request.method, request.url.path, exc)
    if _is_api_request(request):
        return JSONResponse(status_code=503, content={"detail": PUBLIC_INTERNAL_ERROR_MESSAGE})
    return HTMLResponse("本地服务暂时不可用，请稍后再试。", status_code=503)


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception):
    logger.exception("Unhandled API exception: %s %s", request.method, request.url.path)
    if _is_api_request(request):
        return JSONResponse(status_code=500, content={"detail": PUBLIC_INTERNAL_ERROR_MESSAGE})
    return HTMLResponse("本地服务暂时不可用，请稍后再试。", status_code=500)


@app.on_event("startup")
async def on_startup() -> None:
    _install_asyncio_connection_reset_filter()
    repository.init()
    for capture in repository.list(limit=500):
        try:
            if repair_legacy_audio_artifact(capture):
                repository.update(capture.id, result=capture.result)
        except Exception:
            logger.warning(
                "Could not repair legacy audio artifact for capture %s.",
                capture.id,
                exc_info=True,
            )
    recover_pending_captures()
    ensure_worker_started()
    if RUNTIME_TARGET == "windows_desktop":
        import threading
        def warm_local_engine():
            from app.providers.transcription import warm_desktop_transcription
            try:
                warm_desktop_transcription()
            except Exception:
                logger.warning("Local GPU warmup incomplete; explicit CUDA tasks will report the error without CPU fallback.")
        threading.Thread(target=warm_local_engine, name="local-asr-warmup", daemon=True).start()
    logger.info("Capture API startup complete.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    shutdown_browser_runtime()
    from app.sensevoice_cuda import _worker
    if _worker:
        _worker.close()


@app.get("/", response_class=HTMLResponse, response_model=None)
async def serve_root():
    if _dist_available():
        return FileResponse(
            WEB_DIST_DIR / "index.html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    if (FRONTEND_DIR / "capture.html").exists():
        return RedirectResponse(url="/capture.html")
    return HTMLResponse("<h1>万象成文</h1><p>页面正在准备中，请稍后再试。</p>", status_code=200)


@app.get("/brand-mark.svg", include_in_schema=False, response_model=None)
async def serve_brand_mark():
    brand_mark = WEB_DIST_DIR / "brand-mark.svg"
    if not brand_mark.is_file():
        raise HTTPException(status_code=404, detail="品牌资源暂时不可用。")
    return FileResponse(brand_mark, media_type="image/svg+xml")


@app.get("/favicon.ico", include_in_schema=False, response_model=None)
async def serve_favicon():
    """Keep browser fallback favicon requests from becoming noisy 404s."""
    brand_mark = WEB_DIST_DIR / "brand-mark.svg"
    if not brand_mark.is_file():
        raise HTTPException(status_code=404, detail="品牌图标暂时不可用。")
    return FileResponse(brand_mark, media_type="image/svg+xml")


@app.get("/capture.html", response_model=None)
async def serve_legacy_capture():
    if _dist_available():
        return RedirectResponse(url="/")
    legacy = FRONTEND_DIR / "capture.html"
    if not legacy.exists():
        raise HTTPException(status_code=404, detail="页面暂时不可用，请返回首页。")
    return FileResponse(legacy)


@app.get("/c/{capture_id}", response_class=HTMLResponse, response_model=None)
async def serve_capture_result(capture_id: str):
    # Let the SPA own deep-link recovery, including an expired or mistyped
    # capture id. Returning a server 404 here used to bypass React entirely,
    # leaving the browser on a raw error page and preventing the UI from
    # offering its calm "return home" recovery action.
    if _dist_available():
        return FileResponse(
            WEB_DIST_DIR / "index.html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return RedirectResponse(url="/capture.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    frontend_status = _frontend_build_status()
    return {
        "status": "ok",
        "product_name": PRODUCT_NAME,
        "feature_name": PRODUCT_FEATURE_NAME,
        "deployment_mode": DEPLOYMENT_MODE,
        "runtime_target": RUNTIME_TARGET,
        "version": APP_VERSION,
        "commit": APP_COMMIT,
        "frontend_build_id": frontend_status["build_id"],
        "frontend_git_sha": frontend_status["git_sha"],
        "asset_manifest_status": frontend_status["status"],
        "components": _component_status(),
        "browser_runtime": browser_runtime_status(),
        "browser_session": _browser_session_status(),
        "transcription_runtime": transcription_runtime_status(),
    }


@app.get("/config")
async def config() -> dict[str, Any]:
    return _config_payload()


@app.post("/v1/runtime/browser/refresh")
async def refresh_v1_browser_runtime() -> dict[str, Any]:
    """Refresh only the isolated browser context, preserving its profile."""
    if RUNTIME_TARGET not in {"local_web", "windows_desktop"}:
        raise HTTPException(status_code=404, detail="该功能仅在本地运行时可用。")
    try:
        await asyncio.to_thread(refresh_browser_runtime)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="项目级浏览器会话暂时无法刷新。") from exc
    return {"status": "ready", "browser_runtime": browser_runtime_status()}


@app.post("/v1/runtime/browser/session")
async def open_v1_browser_session(payload: BrowserSessionRequest | None = None) -> dict[str, Any]:
    """Open the isolated browser profile for a user-led platform check."""
    global _browser_session_process
    if RUNTIME_TARGET not in {"local_web", "windows_desktop"}:
        raise HTTPException(status_code=404, detail="该功能仅在本地运行时可用。")
    script = PROJECT_ROOT / "scripts" / "refresh_browser_session.py"
    if not script.is_file():
        raise HTTPException(status_code=503, detail="项目级浏览器会话工具还没有准备好。")
    if _browser_session_status()["state"] == "open":
        return {"status": "already_opened"}
    target_url = _validated_browser_session_url(payload.url if payload else None)
    target_file: Path | None = None
    if target_url:
        target_file = TEMP_DIR / f"browser-session-{uuid.uuid4().hex}.json"
        try:
            target_file.write_text(
                json.dumps({"url": target_url}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            raise HTTPException(status_code=503, detail="本地会话准备目录暂时不可用。") from exc
    try:
        await asyncio.to_thread(refresh_browser_runtime)
    except Exception as exc:  # noqa: BLE001
        if target_file:
            target_file.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="项目级浏览器会话暂时无法刷新。") from exc
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    session_log_path = PROJECT_ROOT / "workspace" / "logs" / "browser_session.log"
    session_log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with session_log_path.open("a", encoding="utf-8") as session_log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "scripts.refresh_browser_session",
                    *(["--target-file", str(target_file)] if target_file else []),
                    "--state-file",
                    str(_BROWSER_SESSION_STATE_FILE),
                ],
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=session_log,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
    except OSError as exc:
        if target_file:
            target_file.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="项目级浏览器会话暂时无法打开。") from exc
    await asyncio.sleep(0.5)
    if process.poll() is not None:
        if target_file:
            target_file.unlink(missing_ok=True)
        _BROWSER_SESSION_STATE_FILE.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail="项目级浏览器会话暂未启动，请查看本地诊断后再试。",
        )
    _browser_session_process = process
    try:
        _BROWSER_SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BROWSER_SESSION_STATE_FILE.write_text(
            json.dumps({"pid": int(process.pid), "state": "open"}),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError):
        _BROWSER_SESSION_STATE_FILE.unlink(missing_ok=True)
    return {"status": "opened"}


@app.get("/v1/runtime/packs")
async def list_runtime_packs() -> dict[str, Any]:
    if RUNTIME_TARGET != "windows_desktop":
        raise HTTPException(status_code=404, detail="该功能仅在 Windows 应用中可用。")
    try:
        return {"packs": runtime_pack_manager.status()}
    except RuntimePackError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/runtime/packs/{pack_id}/install")
async def install_runtime_pack(pack_id: str) -> dict[str, Any]:
    if RUNTIME_TARGET != "windows_desktop":
        raise HTTPException(status_code=404, detail="该功能仅在 Windows 应用中可用。")
    try:
        return await asyncio.to_thread(runtime_pack_manager.install, pack_id)
    except RuntimePackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/captures", response_model=CaptureListResponse)
async def list_v1_captures(request: Request) -> CaptureListResponse:
    client_ip = _extract_client_ip(request)
    captures = repository.list(limit=max(CAPTURE_HISTORY_LIMIT * 3, 30), client_ip=client_ip)
    for capture in captures:
        _revive_capture_if_needed(capture)
        _normalize_legacy_user_action_capture(capture)
    captures = repository.list(limit=max(CAPTURE_HISTORY_LIMIT * 3, 30), client_ip=client_ip)
    for capture in captures:
        _normalize_legacy_user_action_capture(capture)
    items = [_public_capture(item) for item in captures if _should_expose_capture_in_recent(item)]
    items = items[:CAPTURE_HISTORY_LIMIT]
    return CaptureListResponse(count=len(items), items=items)


@app.delete("/v1/captures")
async def clear_v1_captures() -> dict[str, Any]:
    cleared = repository.clear_completed()
    return {"cleared": cleared, "history_limit": CAPTURE_HISTORY_LIMIT}


@app.delete("/v1/captures/{capture_id}")
async def delete_v1_capture(capture_id: str) -> dict[str, Any]:
    capture = repository.get(capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="这条记录已经不存在了。")
    if capture.status == "processing":
        raise HTTPException(status_code=409, detail="这条记录还在处理中，暂时不能删除。")
    repository.delete(capture_id)
    return {"deleted": True, "capture_id": capture_id}


@app.get("/v1/captures/{capture_id}", response_model=CaptureEnvelopeModel)
async def get_v1_capture(capture_id: str) -> CaptureEnvelopeModel:
    capture = repository.get(capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")
    _revive_capture_if_needed(capture)
    capture = repository.get(capture_id) or capture
    return _to_capture_envelope(capture)


@app.get("/v1/captures/{capture_id}/events")
async def stream_v1_capture_events(capture_id: str) -> StreamingResponse:
    if repository.get(capture_id) is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")

    async def event_stream():
        last_version = None
        previous_result_state = None
        ready_artifact_types: set[str] = set()
        while True:
            capture = repository.get(capture_id)
            if capture is None:
                payload = {"event": "deleted", "capture_id": capture_id}
                yield f"event: deleted\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            _revive_capture_if_needed(capture)
            _normalize_legacy_user_action_capture(capture)
            capture = repository.get(capture_id) or capture
            _normalize_legacy_user_action_capture(capture)

            envelope = _to_capture_envelope(capture)
            version = envelope.capture.updated_at
            if version != last_version:
                transition_events, ready_artifact_types = _transition_event_names(
                    capture,
                    envelope,
                    previous_result_state=previous_result_state,
                    ready_artifact_types=ready_artifact_types,
                )
                for event_name in transition_events:
                    transition_payload = CaptureEventEnvelopeModel(
                        event=event_name,
                        payload=envelope,
                    ).model_dump(mode="json")
                    yield f"event: {event_name}\ndata: {json.dumps(transition_payload, ensure_ascii=False)}\n\n"
                payload = CaptureEventEnvelopeModel(event="capture.updated", payload=envelope).model_dump(mode="json")
                yield f"event: capture.updated\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_version = version
                previous_result_state = envelope.capture.result_state

            if capture.status in {"failed", "needs_user_action"} or (
                capture.status == "done" and not capture.asset_preparation_pending
            ):
                break
            await asyncio.sleep(1.1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/v1/captures/{capture_id}/artifacts/{artifact_type}")
async def download_v1_artifact(capture_id: str, artifact_type: str) -> FileResponse:
    capture = repository.get(capture_id)
    if capture is None or capture.result is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")

    artifact = next((item for item in capture.result.artifacts if item.type == artifact_type), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail="这个下载项暂时不可用，请重新处理内容。")

    target = Path(artifact.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="下载文件暂时未准备好，请稍后再试。")
    return FileResponse(target, filename=target.name, media_type=artifact.mime_type)


@app.get("/v1/captures/{capture_id}/artifacts/{artifact_type}/preview")
async def preview_v1_artifact(capture_id: str, artifact_type: str) -> FileResponse:
    capture = repository.get(capture_id)
    if capture is None or capture.result is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")

    artifact = next((item for item in capture.result.artifacts if item.type == artifact_type), None)
    if artifact is None or getattr(artifact, "status", "ready") != "ready":
        raise HTTPException(status_code=404, detail="这个下载项暂时不可用，请重新处理内容。")

    target = Path(artifact.path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="下载文件暂时未准备好，请稍后再试。")
    return FileResponse(
        target,
        media_type=artifact.mime_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/v1/captures/{capture_id}/images/{image_index}/thumbnail")
async def preview_v1_capture_image_thumbnail(capture_id: str, image_index: int) -> FileResponse:
    capture = repository.get(capture_id)
    if capture is None or capture.result is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")
    artifact_type = f"image_{image_index:02d}"
    artifact = next(
        (item for item in capture.result.artifacts if item.type == artifact_type),
        None,
    )
    thumbnail_path_value = getattr(artifact, "thumbnail_path", None) if artifact is not None else None
    if not thumbnail_path_value:
        raise HTTPException(status_code=404, detail="图片预览暂时不可用，请稍后再试。")
    target = Path(thumbnail_path_value)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="图片预览暂时不可用，请稍后再试。")
    return FileResponse(
        target,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/v1/captures/{capture_id}/images/{image_index}/retry")
async def retry_v1_capture_image(capture_id: str, image_index: int) -> CaptureEnvelopeModel:
    capture = repository.get(capture_id)
    if capture is None or capture.result is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")
    if capture.source.content_type != "image_article":
        raise HTTPException(status_code=409, detail="这条内容暂时没有可展示的图片。")
    try:
        result = retry_capture_image(capture, image_index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="这张图片暂时未能重新获取，请稍后再试。") from exc
    updated = repository.update(capture_id, result=result)
    if updated is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")
    return _to_capture_envelope(updated)


@app.post("/v1/captures/{capture_id}/cover")
async def ensure_v1_capture_cover(capture_id: str) -> CaptureEnvelopeModel:
    capture = repository.get(capture_id)
    if capture is None or capture.result is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")
    if capture.source.content_type != "audio":
        raise HTTPException(status_code=409, detail="这期内容暂时没有可用封面。")
    try:
        result = await asyncio.to_thread(ensure_capture_cover, capture)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="节目封面暂时未能获取，请稍后再试。") from exc
    updated = repository.update(capture_id, result=result)
    if updated is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")
    return _to_capture_envelope(updated)


@app.post("/v1/captures")
async def create_v1_capture(request: Request, file: UploadFile | None = File(default=None)) -> dict[str, Any]:
    client_ip = _extract_client_ip(request)

    if file is not None:
        file_name, temp_path = await _stream_upload_to_temp(file)
        capture = None
        try:
            capture = repository.create(
                input_type="file",
                source_platform="local_file",
                content_type="unknown",
                file_name=file_name,
                client_ip=client_ip,
            )
            capture_input_dir = repository.capture_workspace(capture.id) / "input"
            capture_input_dir.mkdir(parents=True, exist_ok=True)
            target_path = capture_input_dir / f"{uuid.uuid4().hex}_{file_name}"
            temp_path.replace(target_path)

            repository.update(
                capture.id,
                title=file_name,
                storage_path=str(target_path),
                source=SourceMetaModel(platform="local_file", content_type="unknown", canonical_url=None),
            )
        except Exception:
            _safe_unlink(temp_path)
            if capture is not None:
                shutil.rmtree(repository.capture_workspace(capture.id), ignore_errors=True)
            raise
        _queue_if_needed(capture.id)
        return {
            "capture_id": capture.id,
            "status": capture.status,
            "reused": False,
            "cache_hit": False,
            "source_platform": capture.source_platform,
            "primary_result_type": "",
        }

    payload = await _parse_request_payload(request)
    raw_text = (payload.url or payload.text or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="请提供一个公开链接。")

    try:
        resolved = resolve_url(raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _check_daily_rate_limit(client_ip, platform=resolved.platform)

    cached = _cache_lookup_by_resolved_input(resolved)
    if cached is not None:
        _revive_capture_if_needed(cached)
        cached_result = _normalized_result(cached.result)
        if cached.result is not None and cached_result is not None:
            repository.update(cached.id, result=cached_result.model_copy(update={"cache_hit": True}))
            cached = repository.get(cached.id) or cached
        return {
            "capture_id": cached.id,
            "status": cached.status,
            "reused": True,
            "cache_hit": True,
            "source_platform": cached.source_platform,
            "primary_result_type": cached_result.primary_result_type if cached_result else "",
            "input_warning": resolved.input_warning,
        }

    source = SourceMetaModel(
        platform=resolved.platform,
        content_type=resolved.content_type,
        canonical_url=resolved.normalized_url,
        source_item_id=infer_source_item_id(resolved.platform, resolved.normalized_url) or None,
        raw_input_text=resolved.cleaned_input,
        detected_urls=resolved.detected_urls,
        selection_reason=resolved.selection_reason,
        warnings=[resolved.input_warning] if resolved.input_warning else [],
    )

    identity_hit = _cache_lookup_by_identity(source)
    if identity_hit is not None:
        _revive_capture_if_needed(identity_hit)
        identity_result = _normalized_result(identity_hit.result)
        if identity_result is not None:
            repository.update(identity_hit.id, result=identity_result.model_copy(update={"cache_hit": True}))
        return {
            "capture_id": identity_hit.id,
            "status": identity_hit.status,
            "reused": True,
            "cache_hit": True,
            "source_platform": identity_hit.source_platform,
            "primary_result_type": identity_result.primary_result_type if identity_result else "",
            "input_warning": resolved.input_warning,
        }

    capture = repository.create(
        input_type="url",
        source_platform=resolved.platform,
        content_type=resolved.content_type,
        url=resolved.normalized_url,
        client_ip=client_ip,
    )
    repository.update(capture.id, title=resolved.normalized_url, source=source)
    _queue_if_needed(capture.id)
    return {
        "capture_id": capture.id,
        "status": capture.status,
        "reused": False,
        "cache_hit": False,
        "source_platform": resolved.platform,
        "primary_result_type": "",
        "input_warning": resolved.input_warning,
    }


@app.post("/v1/captures/{capture_id}/retry")
async def retry_v1_capture(capture_id: str) -> dict[str, Any]:
    capture = repository.get(capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="这条内容已经不存在或暂未准备好，请返回首页重新处理。")
    if capture.status == "processing":
        raise HTTPException(status_code=409, detail="这条内容正在处理中，请稍候。")
    if capture.status == "queued":
        raise HTTPException(status_code=409, detail="这条内容已在准备中，请稍候。")

    updated = repository.update(
        capture.id,
        status="queued",
        error_stage=None,
        error_message=None,
            processing=capture.processing.model_copy(
            update={
                "current_stage": "queued",
                "progress_percent": 2,
                "progress_detail": "任务已重新加入队列。",
                "retry_count": capture.processing.retry_count + 1,
                "failure_reason_code": None,
                "retryable": False,
            }
        ),
    )
    _queue_if_needed(capture.id)
    return {"capture_id": capture.id, "status": updated.status if updated else "queued"}

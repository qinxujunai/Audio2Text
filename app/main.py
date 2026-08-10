from __future__ import annotations

import asyncio
import hmac
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.store_fs import CaptureStoreUnavailableError
from app.pipeline import enqueue_capture, ensure_worker_started, recover_pending_captures
from app.resolver import infer_source_item_id, resolve_url
from app.runtime_packs import RuntimePackError, RuntimePackManager
from app.schemas import (
    ArtifactPayloadModel,
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
    PRODUCT_FEATURE_NAME,
    PRODUCT_NAME,
    PRODUCT_SLOGAN,
    PRODUCT_SUMMARY,
    DEPLOYMENT_MODE,
    FFMPEG_PATH,
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
    return WEB_DIST_DIR.exists() and (WEB_DIST_DIR / "index.html").exists()


if _dist_available():
    assets_dir = WEB_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="capture-assets")


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
    return ArtifactPayloadModel(
        type=artifact.type,
        label=artifact.label,
        download_url=artifact.download_url,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        status=getattr(artifact, "status", "ready"),
        optional=bool(getattr(artifact, "optional", False)),
    )


def _result_state(capture, result) -> str:
    if capture.status == "failed":
        return "failed"
    has_text = bool(result and (result.views.primary or result.primary_text).strip())
    declared_state = str(getattr(capture, "result_state", "processing") or "processing")
    if declared_state == "text_ready" and has_text:
        return "text_ready"
    if capture.status == "done" and bool(getattr(capture, "asset_preparation_pending", False)) and has_text:
        return "text_ready"
    if capture.status == "done":
        return "complete"
    return "processing"


def _public_source_images(source) -> list[CaptureSourceImagePayloadModel]:
    image_urls = list(source.image_urls or [])
    live_urls = list(getattr(source, "live_photo_video_urls", []) or [])
    items: list[CaptureSourceImagePayloadModel] = []
    for index, image_url in enumerate(image_urls, start=1):
        cleaned_image_url = (image_url or "").strip()
        if not cleaned_image_url:
            continue
        live_url = (live_urls[index - 1] if index - 1 < len(live_urls) else "").strip() or None
        items.append(
            CaptureSourceImagePayloadModel(
                index=index,
                image_url=cleaned_image_url,
                live_photo_video_url=live_url,
            )
        )
    return items


def _preview_without_title(title: str | None, text: str | None, *, limit: int = 160) -> str:
    cleaned_title = " ".join((title or "").split()).strip().rstrip("…")
    cleaned_text = " ".join((text or "").split()).strip()
    if not cleaned_text:
        return ""
    if cleaned_title and cleaned_text.startswith(cleaned_title):
        cleaned_text = cleaned_text[len(cleaned_title) :].lstrip(" \n:-|：，,。.!?！？")
    return cleaned_text[:limit]


def _to_capture_envelope(capture) -> CaptureEnvelopeModel:
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
            status=capture.status,
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
            error_message=capture.error_message,
            retry_count=capture.processing.retry_count,
            retryable=capture.processing.retryable,
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
            images=_public_source_images(source),
            warnings=source.warnings,
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
        status=capture.status,
        title=capture.title,
        source_platform=capture.source_platform,
        content_type=capture.content_type,
        primary_result_type=result.primary_result_type if result else None,
        created_at=capture.created_at,
        updated_at=capture.updated_at,
        preview_text=preview,
    )


def _should_expose_capture_in_recent(capture) -> bool:
    if capture.status == "failed":
        return False

    title = (capture.title or "").strip().lower()
    description = (capture.source.description or "").strip().lower()
    if capture.source_platform == "xiaohongshu" and (
        "xhslink.com" in title or "xhslink.com" in description
    ):
        return False
    return True


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
        "transcription_available": TRANSCRIPTION_AVAILABLE,
        "transcription_provider": TRANSCRIPTION_PROVIDER,
        "runtime_target": RUNTIME_TARGET,
        "capabilities": {
            "url_capture": True,
            "file_upload": True,
            "source_audio": True,
            "desktop_runtime_management": RUNTIME_TARGET == "windows_desktop",
            "cloud_demo": DEPLOYMENT_MODE == "cloud_preview",
        },
    }


def _component_status() -> dict[str, str]:
    local_runtime = RUNTIME_TARGET in {"local_web", "windows_desktop"}
    return {
        "api": "ready",
        "frontend": "ready" if _dist_available() else "missing",
        "storage": "ready" if repository is not None else "unavailable",
        "transcription": "ready" if TRANSCRIPTION_AVAILABLE else "unavailable",
        "ffmpeg": "ready" if FFMPEG_PATH.exists() else ("missing" if local_runtime else "not_required"),
        "browser": "ready" if PLAYWRIGHT_BROWSERS_DIR.exists() else ("missing" if local_runtime else "not_required"),
        "model": "ready" if MODEL_PATH.exists() else ("missing" if local_runtime else "not_required"),
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
        if cached and _capture_is_reusable(cached):
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
    return HTMLResponse("Service temporarily unavailable.", status_code=503)


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception):
    logger.exception("Unhandled API exception: %s %s", request.method, request.url.path)
    if _is_api_request(request):
        return JSONResponse(status_code=500, content={"detail": PUBLIC_INTERNAL_ERROR_MESSAGE})
    return HTMLResponse("Service temporarily unavailable.", status_code=500)


@app.on_event("startup")
async def on_startup() -> None:
    _install_asyncio_connection_reset_filter()
    repository.init()
    recover_pending_captures()
    ensure_worker_started()
    logger.info("Capture API startup complete.")


@app.get("/", response_class=HTMLResponse, response_model=None)
async def serve_root():
    if _dist_available():
        return FileResponse(WEB_DIST_DIR / "index.html")
    if (FRONTEND_DIR / "capture.html").exists():
        return RedirectResponse(url="/capture.html")
    return HTMLResponse("<h1>Capture API</h1><p>Frontend build not found.</p>", status_code=200)


@app.get("/capture.html", response_model=None)
async def serve_legacy_capture():
    if _dist_available():
        return RedirectResponse(url="/")
    legacy = FRONTEND_DIR / "capture.html"
    if not legacy.exists():
        raise HTTPException(status_code=404, detail="Frontend fallback page not found.")
    return FileResponse(legacy)


@app.get("/c/{capture_id}", response_class=HTMLResponse, response_model=None)
async def serve_capture_result(capture_id: str):
    if repository.get(capture_id) is None:
        raise HTTPException(status_code=404, detail="Capture not found.")
    if _dist_available():
        return FileResponse(WEB_DIST_DIR / "index.html")
    return RedirectResponse(url="/capture.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "product_name": PRODUCT_NAME,
        "feature_name": PRODUCT_FEATURE_NAME,
        "deployment_mode": DEPLOYMENT_MODE,
        "runtime_target": RUNTIME_TARGET,
        "version": APP_VERSION,
        "commit": APP_COMMIT,
        "components": _component_status(),
    }


@app.get("/config")
async def config() -> dict[str, Any]:
    return _config_payload()


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
    captures = repository.list(limit=max(CAPTURE_HISTORY_LIMIT * 3, 30), client_ip=client_ip)
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
        raise HTTPException(status_code=404, detail="Capture not found.")
    _revive_capture_if_needed(capture)
    capture = repository.get(capture_id) or capture
    return _to_capture_envelope(capture)


@app.get("/v1/captures/{capture_id}/events")
async def stream_v1_capture_events(capture_id: str) -> StreamingResponse:
    if repository.get(capture_id) is None:
        raise HTTPException(status_code=404, detail="Capture not found.")

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
            capture = repository.get(capture_id) or capture

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

            if capture.status == "failed" or (capture.status == "done" and not capture.asset_preparation_pending):
                break
            await asyncio.sleep(1.1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/v1/captures/{capture_id}/artifacts/{artifact_type}")
async def download_v1_artifact(capture_id: str, artifact_type: str) -> FileResponse:
    capture = repository.get(capture_id)
    if capture is None or capture.result is None:
        raise HTTPException(status_code=404, detail="Capture not found.")

    artifact = next((item for item in capture.result.artifacts if item.type == artifact_type), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")

    target = Path(artifact.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Artifact file missing.")
    return FileResponse(target, filename=target.name, media_type=artifact.mime_type)


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

    capture = repository.create(
        input_type="url",
        source_platform=resolved.platform,
        content_type=resolved.content_type,
        url=resolved.normalized_url,
        client_ip=client_ip,
    )
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
        raise HTTPException(status_code=404, detail="Capture not found.")
    if capture.status == "processing":
        raise HTTPException(status_code=409, detail="Capture is already processing.")
    if capture.status == "queued":
        raise HTTPException(status_code=409, detail="Capture is already queued.")

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
            }
        ),
    )
    _queue_if_needed(capture.id)
    return {"capture_id": capture.id, "status": updated.status if updated else "queued"}

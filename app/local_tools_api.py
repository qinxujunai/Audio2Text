"""Authenticated local settings and separate, non-destructive proofreading results."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.local_preferences import (cuda_available, read_preferences, save_preferences,
                                   proofreading_config, transcription_options, save_section)
from app.proofreading import ProofreadingService, list_models, verify_proofreading, ProofreadingError, StaleProofreadingError
from app.summary import SummaryService
from app.settings import DEPLOYMENT_MODE, MODEL_PATH, RUNTIME_TARGET, SENSEVOICE_MODEL_DIR
from app.storage import get_capture_repository
from scripts.config import COMPUTE_TYPE, DEVICE

repository = get_capture_repository()
service = ProofreadingService(repository)
summary_service = SummaryService(repository)


def local_only(request: Request):
    if DEPLOYMENT_MODE == "cloud_preview" or (request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}):
        raise HTTPException(403, "此功能只在本地版本提供。")
    origin = request.headers.get("origin")
    desktop_origin = origin in {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"}
    if origin and not desktop_origin:
        # Same-origin local web page only. Avoid drive-by requests reading stored credentials.
        if origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "请求来源未获授权。")
    # WebView2 correctly labels Tauri -> loopback as cross-site. The desktop
    # middleware already validates the exact origin and per-process token.
    if request.headers.get("sec-fetch-site") == "cross-site" and not (RUNTIME_TARGET == "windows_desktop" and desktop_origin):
        raise HTTPException(403, "请求来源未获授权。")


class LocalRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Pydantic's default response includes input values, including API keys.
                raise HTTPException(422, {"code": "invalid_settings", "message": "输入格式不正确，请检查设置后重试。"}) from None
        return safe_handler


router = APIRouter(prefix="/v1/local", dependencies=[Depends(local_only)], route_class=LocalRoute)


class PreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str = "cuda"
    model_path: str = Field(default="", max_length=2000)
    engine: str = "sensevoice"
    sdk_path: str = Field(default="", max_length=2000)
    base_url: str = Field(default="", max_length=2000)
    api_key: str | None = Field(default=None, max_length=8192)
    model: str = Field(default="", max_length=200)


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(max_length=2000)
    api_key: str | None = Field(default=None, max_length=8192)


class CheckRequest(ModelRequest):
    model: str = Field(max_length=200)


class PreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str
    device: str | None = None
    model_path: str | None = Field(default=None, max_length=2000)
    engine: str | None = None
    sdk_path: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=8192)
    model: str | None = Field(default=None, max_length=200)


class AcceptRequest(BaseModel):
    indices: list[int] = Field(max_length=10000)
    job_id: str | None = Field(default=None, max_length=64)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str | None = Field(default=None, max_length=64)


def _check_idle():
    if repository.list_pending_ids():
        raise HTTPException(409, "请等待当前处理任务完成，再更改本地设置。")
    if any(job["status"] == "running" for job in service.jobs.values()):
        raise HTTPException(409, "请等待校对完成或取消后再更改设置。")
    if any(job["status"] == "running" for job in summary_service.jobs.values()):
        raise HTTPException(409, "请等待摘要完成或取消后再更改设置。")


@router.get("/preferences")
def preferences():
    data = read_preferences()
    config = data.get("proofreading", {})
    stored_transcription = data.get("transcription", {})
    options = transcription_options(device=DEVICE, model_path=MODEL_PATH, compute_type=COMPUTE_TYPE,
                                    desktop=RUNTIME_TARGET == "windows_desktop")
    from app.providers.transcription import transcription_runtime_status
    from scripts.config import MODELS_DIR
    candidates = [options["model_path"], MODEL_PATH, SENSEVOICE_MODEL_DIR, MODELS_DIR / "sensevoice" / "small-fp32"]
    model_file = ("model.onnx" if options["execution_device"] == "cuda" else "model.int8.onnx") if options["engine"] == "sensevoice" else "model.bin"
    return {"device": options["device"], "model_path": str(options["model_path"]),
            "engine": options["engine"], "sdk_path": str(options["sdk_path"]),
            "cuda_available": cuda_available(), "model_ready": (options["model_path"] / model_file).is_file(),
            "available_model_paths": sorted({str(p) for p in candidates if any((p / n).is_file() for n in ("model.bin", "model.onnx", "model.int8.onnx"))}),
            "default_paths": {"sensevoice_cpu": stored_transcription.get("sensevoice_cpu_model_path") or str(SENSEVOICE_MODEL_DIR),
                              "sensevoice_cuda": stored_transcription.get("sensevoice_cuda_model_path") or str(MODELS_DIR / "sensevoice" / "small-fp32"),
                              "faster_whisper": str(MODEL_PATH)},
            "base_url": config.get("base_url", ""), "has_api_key": bool(config.get("encrypted_key")),
            "model": config.get("model", ""), "runtime": transcription_runtime_status()}


@router.put("/preferences")
def update_preferences(payload: PreferencesRequest):
    _check_idle()
    try:
        save_preferences(**payload.model_dump())
        return preferences()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/models")
async def detect_models(payload: ModelRequest):
    try:
        config = proofreading_config(base_url=payload.base_url, api_key=payload.api_key)
        return {"models": await asyncio.to_thread(list_models, config)}
    except ProofreadingError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from None
    except httpx.HTTPStatusError as exc:
        message = "密钥未获授权，请检查后再检测。" if exc.response.status_code in {401, 403} else "服务未提供可用模型列表，可以手动填写模型名称。"
        raise HTTPException(400, message) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, "暂时无法连接模型服务，请检查地址或手动填写模型名称。") from exc


@router.patch("/preferences")
def patch_preferences(payload: PreferencesPatch):
    if payload.section == "transcription":
        _check_idle()
    elif any(job["status"] == "running" for job in service.jobs.values()):
        raise HTTPException(409, "请等待校对完成或取消后再更改校对设置。")
    elif any(job["status"] == "running" for job in summary_service.jobs.values()):
        raise HTTPException(409, "请等待摘要完成或取消后再更改校对设置。")
    try:
        values = payload.model_dump(exclude_unset=True)
        section = values.pop("section")
        allowed = ({"base_url", "api_key", "model"} if section == "proofreading"
                   else {"device", "model_path", "engine", "sdk_path"} if section == "transcription" else set())
        if not allowed or not set(values) <= allowed or any(v is None for k, v in values.items() if k != "api_key"):
            raise ValueError("请检查要保存的设置。")
        if section == "transcription":
            current = transcription_options(device=DEVICE, model_path=MODEL_PATH, compute_type=COMPUTE_TYPE,
                                            desktop=RUNTIME_TARGET == "windows_desktop")
            values = {k: values.get(k, str(current[k])) for k in allowed}
        else:
            current = read_preferences().get("proofreading", {})
            values = {"base_url": values.get("base_url", current.get("base_url", "")),
                      "model": values.get("model", current.get("model", "")), "api_key": values.get("api_key")}
        save_section(section, **values)
        # An AI-only update must not depend on loading/validating transcription state.
        if section == "proofreading":
            stored = read_preferences()["proofreading"]
            return {"section": section, "base_url": stored["base_url"], "model": stored["model"],
                    "has_api_key": bool(stored["encrypted_key"]), "status": "saved"}
        return preferences()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/proofreading-check")
async def proofreading_check(payload: CheckRequest):
    try:
        config = proofreading_config(base_url=payload.base_url, api_key=payload.api_key, model=payload.model)
        return await asyncio.to_thread(verify_proofreading, config)
    except ProofreadingError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/cuda-check")
async def cuda_check():
    _check_idle()
    try:
        if preferences()["device"] != "cuda":
            raise ValueError("请先保存 CUDA 设置，再验证引擎。")
        def check():
            from app.providers.transcription import get_transcription_provider, LocalSenseVoiceCudaProvider, _transcription_runtime
            provider = get_transcription_provider()
            if isinstance(provider, LocalSenseVoiceCudaProvider):
                runtime = provider.worker.warm()
                _transcription_runtime.update(active_provider=provider.name, actual_device="cuda", compute_type="float32", fallback_active=False)
                return runtime
            from scripts.model_loader import get_model, get_model_runtime
            import numpy as np
            model = get_model()
            segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1, vad_filter=False)
            list(segments)
            return get_model_runtime()
        runtime = await asyncio.to_thread(check)
        return {"device": runtime["device"], "compute_type": runtime["compute_type"], "model": runtime.get("model") or Path(runtime["model_path"]).name}
    except Exception as exc:
        raise HTTPException(400, "CUDA 验证未通过，请检查本地模型与 CUDA 运行组件；未切换到 CPU。") from exc


@router.get("/captures/{capture_id}/summary")
def summary_status(capture_id: str):
    try:
        return summary_service.status(capture_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/summary/config")
def summary_config():
    try:
        return summary_service.preview()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


class SummaryStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_id: str = Field(min_length=64, max_length=64)


@router.post("/captures/{capture_id}/summary")
def start_summary(capture_id: str, payload: SummaryStartRequest):
    try:
        return summary_service.start(capture_id, payload.config_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/captures/{capture_id}/summary/cancel")
def cancel_summary(capture_id: str, payload: CancelRequest):
    try:
        return summary_service.cancel(capture_id, payload.job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/captures/{capture_id}/proofreading")
def proofreading_status(capture_id: str):
    try:
        return service.status(capture_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/captures/{capture_id}/proofreading")
def start_proofreading(capture_id: str):
    try:
        return service.start(capture_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/captures/{capture_id}/proofreading/cancel")
def cancel_proofreading(capture_id: str, payload: CancelRequest):
    try:
        service.cancel(capture_id, job_id=payload.job_id)
    except StaleProofreadingError as exc:
        raise HTTPException(409, {"code": "stale_job", "message": str(exc)}) from None
    return {"cancelled": True}


@router.post("/captures/{capture_id}/proofreading/accept")
def accept_proofreading(capture_id: str, payload: AcceptRequest):
    try:
        return service.accept(capture_id, payload.indices, job_id=payload.job_id)
    except StaleProofreadingError as exc:
        raise HTTPException(409, {"code": "stale_suggestions", "message": str(exc)}) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/captures/{capture_id}/proofreading/download")
def download_proofreading(capture_id: str, version: str = "corrected"):
    try:
        text = service.source(capture_id)
        if version == "corrected":
            saved = service.saved(capture_id)
            if not saved:
                raise ValueError("请先确认建议并保存校对版。")
            text = saved["text"]
        elif version != "original":
            raise ValueError("请选择原文或校对版。")
        return Response(content=text.encode("utf-8-sig"), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{capture_id}_{version}.txt"',
                                 "Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/captures/{capture_id}/proofreading/text")
def proofreading_text(capture_id: str, version: str = "corrected"):
    response = download_proofreading(capture_id, version)
    return {"text": response.body.decode("utf-8-sig")}

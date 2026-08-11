from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.settings import (
    ALLOW_DEGRADED_START,
    BROWSER_PROFILE_DIR,
    DEVICE,
    FFMPEG_PATH,
    FFMPEG_PATH_SOURCE,
    MODEL_PATH,
    MODEL_PATH_IS_LEGACY_FALLBACK,
    OPENAI_COMPATIBLE_API_KEY,
    OPENAI_COMPATIBLE_BASE_URL,
    OPENAI_COMPATIBLE_MODEL,
    PLAYWRIGHT_BROWSERS_DIR,
    SENSEVOICE_MODEL_DIR,
    TRANSCRIPTION_PROVIDER,
)
from scripts.logger import get_logger

logger = get_logger("runtime_preflight", "runtime_preflight.log")

CUDA_RUNTIME_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")
_DLL_DIRECTORY_HANDLES: list[object] = []


@dataclass(slots=True)
class PreflightResult:
    fatal_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fatal_errors


def _has_playwright_chromium() -> bool:
    if not PLAYWRIGHT_BROWSERS_DIR.exists():
        return False
    try:
        return any(item.name.startswith("chromium") for item in PLAYWRIGHT_BROWSERS_DIR.iterdir())
    except OSError:
        return False


def _has_system_chromium() -> bool:
    if not sys.platform.startswith("win"):
        return bool(shutil.which("microsoft-edge") or shutil.which("google-chrome") or shutil.which("chromium"))
    roots = [
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    relative_paths = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
    )
    return any(Path(root, relative).is_file() for root in roots if root for relative in relative_paths)


def browser_runtime_available() -> bool:
    return _has_playwright_chromium() or _has_system_chromium()


def _browser_profile_has_session_state() -> bool:
    if not BROWSER_PROFILE_DIR.exists():
        return False

    sentinel_paths = (
        BROWSER_PROFILE_DIR / "Local State",
        BROWSER_PROFILE_DIR / "Default" / "Preferences",
        BROWSER_PROFILE_DIR / "Default" / "Cookies",
        BROWSER_PROFILE_DIR / "Default" / "Network" / "Cookies",
        BROWSER_PROFILE_DIR / "Default" / "Web Data",
        BROWSER_PROFILE_DIR / "Default" / "Login Data",
    )
    if any(path.exists() for path in sentinel_paths):
        return True

    try:
        return any(item.is_file() for item in BROWSER_PROFILE_DIR.rglob("*"))
    except OSError:
        return False


def _check_transcription_provider(result: PreflightResult) -> None:
    if TRANSCRIPTION_PROVIDER == "openai_compatible":
        missing = []
        if not OPENAI_COMPATIBLE_BASE_URL:
            missing.append("openai_compatible_base_url")
        if not OPENAI_COMPATIBLE_API_KEY:
            missing.append("openai_compatible_api_key")
        if not OPENAI_COMPATIBLE_MODEL:
            missing.append("openai_compatible_model")
        if missing:
            message = "OpenAI-compatible 转写模式未配置完整，缺少: " + ", ".join(missing)
            if ALLOW_DEGRADED_START:
                result.warnings.append(message + "。当前按云端预览模式启动，转写任务会给出明确失败提示。")
            else:
                result.fatal_errors.append(message)
        return

    faster_whisper_ready = MODEL_PATH.is_dir()
    sensevoice_ready = (SENSEVOICE_MODEL_DIR / "model.int8.onnx").is_file() and (
        SENSEVOICE_MODEL_DIR / "tokens.txt"
    ).is_file()

    if TRANSCRIPTION_PROVIDER == "local_sensevoice":
        if sensevoice_ready:
            return
        message = f"SenseVoice 模型目录不完整: {SENSEVOICE_MODEL_DIR}。"
        if ALLOW_DEGRADED_START:
            result.warnings.append(message + "当前将以受限模式启动，请在应用内重新安装运行组件。")
        else:
            result.fatal_errors.append(message)
        return

    if TRANSCRIPTION_PROVIDER == "auto" and sensevoice_ready and not (
        DEVICE == "cuda" and faster_whisper_ready
    ):
        return

    if not faster_whisper_ready:
        message = (
            f"本地转写模型目录不存在: {MODEL_PATH}。请把模型放到该目录，或通过 "
            "audio2text.settings.json / AUDIO2TEXT_MODEL_PATH 显式指定。"
        )
        if TRANSCRIPTION_PROVIDER == "auto":
            message = (
                f"本地转写模型尚未就绪: SenseVoice={SENSEVOICE_MODEL_DIR}，"
                f"Faster-Whisper={MODEL_PATH}。"
            )
        if ALLOW_DEGRADED_START:
            result.warnings.append(message + "当前将以受限模式启动，请在应用内安装运行组件。")
        else:
            result.fatal_errors.append(message)
        return

    if MODEL_PATH_IS_LEGACY_FALLBACK:
        result.warnings.append(
            f"当前仍在使用仓库内 legacy 模型目录: {MODEL_PATH}。正式部署建议改为 workspace/runtime/models 下的项目级模型目录。"
        )

    _check_cuda_runtime(result)


def _candidate_runtime_dirs() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("CUDA_RUNTIME_PATH", "CUDNN_PATH"):
        raw_value = _env_value(env_name).strip()
        if not raw_value:
            continue
        root = Path(raw_value)
        candidates.append(root)
        candidates.append(root / "bin")
    for raw_path in _env_value("PATH").split(os.pathsep):
        if raw_path.strip():
            candidates.append(Path(raw_path.strip()))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def _windows_user_env_value(name: str) -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return str(value or "")


def _env_value(name: str) -> str:
    values = [os.environ.get(name, ""), _windows_user_env_value(name)]
    return os.pathsep.join(value for value in values if value)


def _find_runtime_file(filename: str) -> Path | None:
    for folder in _candidate_runtime_dirs():
        path = folder / filename
        if path.exists():
            return path
    system_match = shutil.which(filename)
    return Path(system_match) if system_match else None


def _cuda_runtime_dirs() -> list[Path]:
    runtime_dirs: list[Path] = []
    seen: set[str] = set()
    for folder in _candidate_runtime_dirs():
        try:
            folder = folder.resolve()
        except OSError:
            continue
        if not folder.is_dir():
            continue
        if not any((folder / filename).exists() for filename in CUDA_RUNTIME_DLLS):
            continue
        key = str(folder).lower()
        if key in seen:
            continue
        runtime_dirs.append(folder)
        seen.add(key)
    return runtime_dirs


def apply_cuda_runtime_to_env(env: dict[str, str]) -> list[Path]:
    runtime_dirs = _cuda_runtime_dirs()
    if not runtime_dirs:
        return []

    existing_path = env.get("PATH", "")
    existing_parts = [item for item in existing_path.split(os.pathsep) if item]
    existing_keys = {item.lower() for item in existing_parts}
    prepend_parts = [str(path) for path in runtime_dirs if str(path).lower() not in existing_keys]
    if prepend_parts:
        env["PATH"] = os.pathsep.join([*prepend_parts, *existing_parts])
    return runtime_dirs


def configure_cuda_runtime_search_path() -> list[Path]:
    runtime_dirs = apply_cuda_runtime_to_env(os.environ)
    if not runtime_dirs or not sys.platform.startswith("win") or not hasattr(os, "add_dll_directory"):
        return runtime_dirs

    for folder in runtime_dirs:
        try:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(folder)))
        except OSError:
            logger.warning(f"CUDA 运行时目录无法加入 DLL 搜索路径: {folder}")
    return runtime_dirs


def _check_cuda_runtime(result: PreflightResult) -> None:
    if DEVICE not in {"cuda", "auto"}:
        return
    if DEVICE == "auto":
        return
    if not sys.platform.startswith("win"):
        if shutil.which("nvidia-smi") is None:
            result.warnings.append("当前配置为 CUDA 转写，但未在 PATH 中检测到 nvidia-smi；请确认容器或主机 GPU runtime 已就绪。")
        return

    missing = [filename for filename in CUDA_RUNTIME_DLLS if _find_runtime_file(filename) is None]
    if not missing:
        return

    message = (
        "当前配置为 CUDA 转写，但未在 CUDA_RUNTIME_PATH / CUDNN_PATH / PATH 中检测到必要运行时: "
        + ", ".join(missing)
        + "。请先配置项目启动脚本或用户环境变量，否则 faster-whisper 会无法使用 GPU。"
    )
    if ALLOW_DEGRADED_START:
        result.warnings.append(message)
    else:
        result.fatal_errors.append(message)


def _check_playwright_runtime(result: PreflightResult) -> None:
    if browser_runtime_available():
        return
    result.warnings.append(
        f"Playwright Chromium 运行时未安装到项目目录: {PLAYWRIGHT_BROWSERS_DIR}。抖音 / 小红书浏览器提取暂不可用。"
    )


def _check_browser_profile(result: PreflightResult) -> None:
    if _browser_profile_has_session_state():
        return
    result.warnings.append(
        f"项目级浏览器会话目录当前为空: {BROWSER_PROFILE_DIR}。高波动平台将优先只走公开链路；如需更高成功率，请先补齐项目级浏览器会话。"
    )


def _check_ffmpeg(result: PreflightResult) -> None:
    if FFMPEG_PATH.exists():
        return

    system_ffmpeg = shutil.which("ffmpeg")
    if FFMPEG_PATH_SOURCE == "explicit":
        result.warnings.append(
            f"显式配置的 ffmpeg 不可用: {FFMPEG_PATH}。请检查 audio2text.settings.json / AUDIO2TEXT_FFMPEG_PATH。"
        )
        return

    if system_ffmpeg:
        result.warnings.append(
            f"检测到系统 ffmpeg({system_ffmpeg})，但当前版本默认不依赖宿主机 PATH。请把 ffmpeg 放到 {FFMPEG_PATH}，或显式设置 AUDIO2TEXT_FFMPEG_PATH。"
        )
        return

    result.warnings.append(
        f"项目级 ffmpeg 未就绪: {FFMPEG_PATH}。涉及媒体下载 / 转封装的链路可能不可用。"
    )


def run_runtime_preflight() -> PreflightResult:
    result = PreflightResult()
    _check_transcription_provider(result)
    _check_playwright_runtime(result)
    _check_browser_profile(result)
    _check_ffmpeg(result)
    return result


def ensure_runtime_ready() -> PreflightResult:
    result = run_runtime_preflight()
    for message in result.warnings:
        logger.warning(message)
    if result.fatal_errors:
        for message in result.fatal_errors:
            logger.error(message)
        raise RuntimeError("\n".join(result.fatal_errors))

    logger.info("Runtime preflight passed.")
    return result

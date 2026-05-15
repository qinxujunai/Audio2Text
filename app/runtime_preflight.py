from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from app.settings import (
    ALLOW_DEGRADED_START,
    BROWSER_PROFILE_DIR,
    FFMPEG_PATH,
    FFMPEG_PATH_SOURCE,
    MODEL_PATH,
    MODEL_PATH_IS_LEGACY_FALLBACK,
    OPENAI_COMPATIBLE_API_KEY,
    OPENAI_COMPATIBLE_BASE_URL,
    OPENAI_COMPATIBLE_MODEL,
    PLAYWRIGHT_BROWSERS_DIR,
    TRANSCRIPTION_PROVIDER,
)
from scripts.logger import get_logger

logger = get_logger("runtime_preflight", "runtime_preflight.log")


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

    if not MODEL_PATH.exists() or not MODEL_PATH.is_dir():
        message = (
            f"本地转写模型目录不存在: {MODEL_PATH}。请把模型放到该目录，或通过 "
            "audio2text.settings.json / AUDIO2TEXT_MODEL_PATH 显式指定。"
        )
        if ALLOW_DEGRADED_START:
            result.warnings.append(message + "当前按云端预览模式启动，转写任务会给出明确失败提示。")
        else:
            result.fatal_errors.append(message)
        return

    if MODEL_PATH_IS_LEGACY_FALLBACK:
        result.warnings.append(
            f"当前仍在使用仓库内 legacy 模型目录: {MODEL_PATH}。正式部署建议改为 workspace/runtime/models 下的项目级模型目录。"
        )


def _check_playwright_runtime(result: PreflightResult) -> None:
    if _has_playwright_chromium():
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

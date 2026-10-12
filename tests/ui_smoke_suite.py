from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

# Keep direct file invocation (`python tests/ui_smoke_suite.py`) equivalent to
# module invocation by making the repository root importable before app imports.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas import (
    ArtifactModel,
    CaptureModel,
    ProcessingStateModel,
    ResultDocumentModel,
    ResultSegmentModel,
    ResultViewsModel,
    SourceMetaModel,
)


SVG_IMAGE_BYTES = b"""<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000">
  <rect width="1600" height="1000" fill="#d8c098"/>
  <rect x="120" y="120" width="1360" height="760" rx="48" fill="#8fb0a2"/>
  <circle cx="420" cy="440" r="160" fill="#f2e5bf"/>
  <path d="M700 740 C860 380 1040 560 1240 260" fill="none" stroke="#3f4d44" stroke-width="52" stroke-linecap="round"/>
  <text x="140" y="910" font-family="serif" font-size="72" fill="#222">Praxis fixture image</text>
</svg>"""
VIDEO_FIXTURE_BYTES = (Path(__file__).resolve().parent / "fixtures" / "ui-preview.mp4").read_bytes()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _python_executable() -> Path:
    if os.name == "nt":
        return _project_root() / ".venv" / "Scripts" / "python.exe"
    return _project_root() / ".venv" / "bin" / "python"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, *, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    with httpx.Client(trust_env=False, timeout=5.0) as client:
        while time.time() < deadline:
            try:
                response = client.get(url)
                response.raise_for_status()
                return
            except Exception as exc:  # pragma: no cover - only used on startup failures
                last_error = exc
                time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _terminate_process_tree(process: subprocess.Popen[str] | None, *, timeout: float = 10.0) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()


def _cleanup_temporary_workspace(directory):
    for attempt in range(20):
        try:
            return directory.cleanup()
        except PermissionError:
            if attempt == 19:
                raise
            # Windows child process log handles can outlive the wrapper PID.
            time.sleep(.25)


def _seed_capture(workspace_dir: Path, capture: CaptureModel) -> None:
    capture_path = workspace_dir / "captures" / capture.id / "capture.json"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_text(capture.model_dump_json(indent=2), encoding="utf-8")


def _artifact(
    workspace_dir: Path,
    capture_id: str,
    artifact_type: str,
    filename: str,
    *,
    mime_type: str,
    content: bytes,
) -> ArtifactModel:
    artifact_path = workspace_dir / "artifacts" / capture_id / filename
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(content)
    return ArtifactModel(
        type=artifact_type,
        label=filename,
        path=str(artifact_path),
        download_url=f"/v1/captures/{capture_id}/artifacts/{artifact_type}",
        mime_type=mime_type,
        size_bytes=len(content),
    )


def _video_capture(workspace_dir: Path, capture_id: str, *, created_at: str, title: str) -> CaptureModel:
    paragraphs = [
        "如果你不花时间去创造你想要的生活",
        "那么你将被迫花更多的时间",
        "去应付你所不想要的生活",
        "成功的路上没有人会叫你起床",
        "也没有人会为你买单",
        "你需要自我管理 自我突破",
        "一个人的斗志是最容易被等待和拖延压垮的",
        "犹豫不决是你人生最大的敌人",
        "不管在什么情况下",
        "我们都应该把精力用来提升自己",
        "路虽远 行则将至 势虽难 做则必成",
        "把每一次向前走的小动作，变成真正能落地的结果。",
    ]
    primary_text = "\n\n".join(paragraphs)
    markdown_text = "# 想要的生活\n\n" + "\n\n".join(paragraphs)
    artifacts = [
        _artifact(
            workspace_dir,
            capture_id,
            "txt",
            "capture.txt",
            mime_type="text/plain",
            content=primary_text.encode("utf-8"),
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "md",
            "capture.md",
            mime_type="text/markdown",
            content=markdown_text.encode("utf-8"),
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "preview_media",
            "preview_media.mp4",
            mime_type="video/mp4",
            content=VIDEO_FIXTURE_BYTES,
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "source_media",
            "source_media.mp4",
            mime_type="video/mp4",
            content=VIDEO_FIXTURE_BYTES,
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "source_audio",
            "source_audio.m4a",
            mime_type="audio/mp4",
            content=b"fake-source-audio",
        ),
    ]
    return CaptureModel(
        id=capture_id,
        input_type="url",
        status="done",
        title=title,
        source_platform="douyin",
        content_type="video",
        url=f"https://example.com/{capture_id}",
        created_at=created_at,
        updated_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        source=SourceMetaModel(
            platform="douyin",
            content_type="video",
            canonical_url=f"https://example.com/{capture_id}",
            author="Praxis AI",
            duration_seconds=60,
            description="这是一条用于 UI smoke 的稳定结果样例。",
            extractor_used="fixture",
        ),
        processing=ProcessingStateModel(
            current_stage="completed",
            progress_percent=100,
            progress_detail="整理完成",
            completed_stages=["resolve", "extract", "compose"],
        ),
        result=ResultDocumentModel(
            primary_text=primary_text,
            primary_result_type="transcript",
            confidence="high",
            completeness="full",
            transcript_status="transcribed",
            text_source="transcript",
            result_notice="",
            markdown_text=markdown_text,
            views=ResultViewsModel(primary=primary_text, markdown=markdown_text),
            content_facts=[
                {"key": "title", "label": "标题", "value": title},
                {"key": "platform", "label": "平台", "value": "抖音"},
                {"key": "content_type", "label": "内容类型", "value": "视频"},
                {"key": "author", "label": "作者", "value": "Praxis AI"},
                {"key": "duration", "label": "时长", "value": "1 分钟"},
            ],
            artifacts=artifacts,
        ),
    )


def _video_text_only_capture(
    workspace_dir: Path,
    capture_id: str,
    *,
    created_at: str,
    title: str,
) -> CaptureModel:
    capture = _video_capture(
        workspace_dir,
        capture_id,
        created_at=created_at,
        title=title,
    )
    capture.result.artifacts = [
        artifact
        for artifact in capture.result.artifacts
        if artifact.type not in {"preview_media", "source_media", "source_audio"}
    ]
    capture.result.result_notice = "文本已整理完成，但视频文件暂未就绪。"
    return capture


def _audio_capture(workspace_dir: Path, capture_id: str, *, created_at: str, title: str) -> CaptureModel:
    segment_texts = [
        "小宇宙音频样例已经转写完成。",
        "这里保留完整正文，用于验证播放器和时间跳转。",
        "播客的价值不仅在于声音，也在于可以被检索和引用。",
        "封面、节目名称和作者共同帮助用户确认内容来源。",
        "播放进度应当与带时间的正文保持同步。",
        "点击正文段落时，播放器需要准确跳到对应位置。",
        "在移动端离开播放器区域后，紧凑控制条才应该出现。",
        "返回播放器区域时，紧凑控制条应当自动收起。",
    ]
    primary_text = "\n\n".join(segment_texts)
    artifacts = [
        _artifact(
            workspace_dir,
            capture_id,
            "txt",
            "capture.txt",
            mime_type="text/plain",
            content=primary_text.encode("utf-8"),
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "source_audio",
            "episode.m4a",
            mime_type="audio/mp4",
            content=b"fake-podcast-audio",
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "cover_image",
            "cover.svg",
            mime_type="image/svg+xml",
            content=SVG_IMAGE_BYTES,
        ),
    ]
    return CaptureModel(
        id=capture_id,
        input_type="url",
        status="done",
        title=title,
        source_platform="xiaoyuzhou",
        content_type="audio",
        url=f"https://example.com/{capture_id}",
        created_at=created_at,
        updated_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        source=SourceMetaModel(
            platform="xiaoyuzhou",
            content_type="audio",
            canonical_url=f"https://example.com/{capture_id}",
            author="Praxis AI",
            duration_seconds=128,
            description="音频交付样例。",
            extractor_used="fixture",
        ),
        processing=ProcessingStateModel(
            current_stage="completed",
            progress_percent=100,
            progress_detail="整理完成",
            completed_stages=["resolve", "extract", "transcribe", "compose"],
        ),
        result=ResultDocumentModel(
            primary_text=primary_text,
            primary_result_type="transcript",
            confidence="high",
            completeness="full",
            transcript_status="transcribed",
            text_source="transcript",
            segments=[
                ResultSegmentModel(
                    kind="timed_text",
                    origin="transcript",
                    text=text,
                    start_sec=index * 16,
                    end_sec=(index + 1) * 16,
                )
                for index, text in enumerate(segment_texts)
            ],
            views=ResultViewsModel(primary=primary_text),
            content_facts=[
                {"key": "title", "label": "标题", "value": title},
                {"key": "platform", "label": "平台", "value": "小宇宙"},
                {"key": "content_type", "label": "内容类型", "value": "音频"},
                {"key": "duration", "label": "时长", "value": "2 分钟"},
            ],
            artifacts=artifacts,
        ),
    )


def _image_capture(
    workspace_dir: Path,
    capture_id: str,
    *,
    created_at: str,
    title: str,
    primary_text: str = "",
) -> CaptureModel:
    artifacts = [
        _artifact(
            workspace_dir,
            capture_id,
            "image_1",
            "image_1.svg",
            mime_type="image/svg+xml",
            content=SVG_IMAGE_BYTES,
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "image_live_1",
            "image_live_1.mp4",
            mime_type="video/mp4",
            content=b"fake-live-photo-video",
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "image_2",
            "image_2.svg",
            mime_type="image/svg+xml",
            content=SVG_IMAGE_BYTES,
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "images_zip",
            "images.zip",
            mime_type="application/zip",
            content=b"fake-zip",
        ),
    ]
    return CaptureModel(
        id=capture_id,
        input_type="url",
        status="done",
        title=title,
        source_platform="xiaohongshu",
        content_type="image_article",
        url=f"https://example.com/{capture_id}",
        created_at=created_at,
        updated_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        source=SourceMetaModel(
            platform="xiaohongshu",
            content_type="image_article",
            canonical_url=f"https://example.com/{capture_id}",
            author="Praxis AI",
            description="图文与 Live 图交付样例。",
            image_urls=["https://example.com/fixture-image.png", "https://example.com/fixture-image-2.png"],
            extractor_used="fixture",
        ),
        processing=ProcessingStateModel(
            current_stage="completed",
            progress_percent=100,
            progress_detail="整理完成",
            completed_stages=["resolve", "extract", "compose"],
        ),
        result=ResultDocumentModel(
            primary_text=primary_text,
            primary_result_type="article",
            confidence="high",
            completeness="full",
            transcript_status="skipped",
            text_source="article",
            views=ResultViewsModel(primary=primary_text),
            content_facts=[
                {"key": "title", "label": "标题", "value": title},
                {"key": "platform", "label": "平台", "value": "小红书"},
                {"key": "content_type", "label": "内容类型", "value": "图文"},
            ],
            artifacts=artifacts,
        ),
    )


def _simple_capture(workspace_dir: Path, capture_id: str, *, created_at: str, title: str, platform: str, content_type: str) -> CaptureModel:
    preview = f"{title} 的最近记录预览，用于首页历史卡片回归。"
    artifacts = [
        _artifact(
            workspace_dir,
            capture_id,
            "txt",
            "capture.txt",
            mime_type="text/plain",
            content=preview.encode("utf-8"),
        )
    ]
    return CaptureModel(
        id=capture_id,
        input_type="url",
        status="done",
        title=title,
        source_platform=platform,
        content_type=content_type,
        url=f"https://example.com/{capture_id}",
        created_at=created_at,
        updated_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        source=SourceMetaModel(
            platform=platform,
            content_type=content_type,
            canonical_url=f"https://example.com/{capture_id}",
            description=preview,
            extractor_used="fixture",
        ),
        processing=ProcessingStateModel(
            current_stage="completed",
            progress_percent=100,
            progress_detail="整理完成",
            completed_stages=["resolve", "extract", "compose"],
        ),
        result=ResultDocumentModel(
            primary_text=preview,
            primary_result_type="article",
            confidence="high",
            completeness="full",
            transcript_status="skipped",
            text_source="article",
            views=ResultViewsModel(primary=preview),
            artifacts=artifacts,
        ),
    )


def _failed_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    return CaptureModel(
        id=capture_id,
        input_type="url",
        status="needs_user_action",
        result_state="awaiting_user",
        title="这条失败记录不应出现在首页最近记录里",
        source_platform="youtube",
        content_type="video",
        url=f"https://example.com/{capture_id}",
        created_at=created_at,
        updated_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        error_stage="extract",
        error_message="示例失败",
        source=SourceMetaModel(
            platform="youtube",
            content_type="video",
            canonical_url=f"https://example.com/{capture_id}",
        ),
        processing=ProcessingStateModel(
            current_stage="awaiting_user",
            progress_percent=18,
            failure_reason_code="browser_challenge_required",
            retryable=True,
        ),
        result=None,
    )


def _legacy_platform_failure_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    capture = _failed_capture(capture_id, created_at=created_at)
    capture.status = "failed"
    capture.result_state = "failed"
    return capture


def _permanent_failure_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    capture = _failed_capture(capture_id, created_at=created_at)
    capture.status = "failed"
    capture.result_state = "failed"
    capture.error_message = "这条内容当前已经失效或不可访问，请换一条公开内容再试。"
    capture.processing.current_stage = "failed"
    capture.processing.progress_percent = 0
    capture.processing.failure_reason_code = "source_not_found"
    capture.processing.retryable = False
    return capture


def _transient_connection_failure_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    capture = _permanent_failure_capture(capture_id, created_at=created_at)
    capture.title = "来源连接暂时不稳定"
    capture.error_message = "当前网络环境没有稳定连上来源站点，请稍后重试。"
    # Exercise the platform-qualified form emitted by external extraction
    # adapters, not only the legacy generic reason code.
    capture.processing.failure_reason_code = "douyin_extract_network_failed"
    capture.processing.retryable = True
    return capture


def _retry_exhausted_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    capture = _transient_connection_failure_capture(capture_id, created_at=created_at)
    capture.status = "needs_user_action"
    capture.result_state = "awaiting_user"
    capture.error_message = "连接仍不稳定，已自动重试；你可以重新处理，或换一条公开内容。"
    capture.processing.current_stage = "awaiting_user"
    capture.processing.progress_percent = 18
    capture.processing.failure_reason_code = "retry_exhausted"
    capture.processing.retryable = True
    return capture


def _transcription_runtime_unavailable_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    capture = _permanent_failure_capture(capture_id, created_at=created_at)
    capture.title = "转写组件尚未准备"
    capture.error_stage = "transcribe"
    capture.error_message = "转写运行时还没有准备好，媒体文件已保留。请稍后重试。"
    capture.processing.failure_reason_code = "transcribe_runtime_unavailable"
    capture.processing.retryable = False
    return capture


def _xiaohongshu_short_link_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    url = "https://xhslink.com/a/example"
    return CaptureModel(
        id=capture_id,
        input_type="url",
        status="needs_user_action",
        result_state="awaiting_user",
        title="小红书短链需要补充完整链接",
        source_platform="xiaohongshu",
        content_type="webpage",
        url=url,
        created_at=created_at,
        updated_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        error_stage="extract",
        error_message="这条小红书内容请从浏览器地址栏复制完整网页链接后再试。",
        source=SourceMetaModel(
            platform="xiaohongshu",
            content_type="webpage",
            canonical_url=url,
        ),
        processing=ProcessingStateModel(
            current_stage="awaiting_user",
            progress_percent=18,
            failure_reason_code="xiaohongshu_full_url_required",
            retryable=False,
        ),
        result=None,
    )


def _processing_capture(capture_id: str, *, created_at: str) -> CaptureModel:
    return CaptureModel(
        id=capture_id,
        input_type="url",
        status="processing",
        result_state="processing",
        title="正在整理的处理中样例",
        source_platform="youtube",
        content_type="video",
        url=f"https://example.com/{capture_id}",
        created_at=created_at,
        updated_at=created_at,
        started_at=created_at,
        source=SourceMetaModel(
            platform="youtube",
            content_type="video",
            canonical_url=f"https://example.com/{capture_id}",
        ),
        processing=ProcessingStateModel(
            current_stage="transcribe",
            progress_percent=52,
            # A provider must never be able to leak an internal failure detail
            # into the calm in-progress surface.
            progress_detail="转写失败：provider timeout",
            completed_stages=["resolve", "extract"],
        ),
        result=None,
    )


def _seed_workspace(workspace_dir: Path) -> None:
    for relative in (
        "captures",
        "artifacts",
        "logs",
        "temp",
        "output",
        "cache",
        "runtime/browser-profile",
    ):
        (workspace_dir / relative).mkdir(parents=True, exist_ok=True)

    captures = [
        _video_capture(workspace_dir, "demovideo001", created_at="2026-04-11 10:00:00", title="你只能靠自己得到自己想要的情感共鸣 语录"),
        _video_text_only_capture(
            workspace_dir,
            "demovideotextonly001",
            created_at="2026-04-11 09:59:45",
            title="文字已完成但视频仍在准备",
        ),
        _audio_capture(workspace_dir, "demoaudio001", created_at="2026-04-11 09:59:30", title="蚂蚁测试 AI 版支付宝，市场监管总局约谈山姆"),
        _image_capture(workspace_dir, "demoimage001", created_at="2026-04-11 09:59:00", title="图文整理结果样例"),
        _image_capture(
            workspace_dir,
            "demoimagelong001",
            created_at="2026-04-11 09:58:30",
            title="有人会 get 这张图片，并拍过类似的构图（小船）",
            primary_text="\n\n".join(
                [
                    "船儿轻晃在粉蓝色的晚风中",
                    "偏爱坐在这一叶扁舟里",
                    "独享一场橘粉交织的日落盛宴",
                    "落日揉碎余晖洒落在海面",
                    "波光粼粼，晚风温柔",
                    "喧嚣的世界好像瞬间慢了下来",
                    "所有的焦虑、疲惫与烦躁",
                    "都被傍晚的落日悄悄治愈",
                    "原来平凡日子里的美好",
                    "从来都藏在转瞬即逝的黄昏里",
                    "愿每一次驻足都能看见生活的光",
                    "也愿每一段文字都值得被好好阅读",
                ]
            ),
        ),
        _simple_capture(
            workspace_dir,
            "recenttxt001",
            created_at="2026-04-11 09:58:00",
            title="男人需要的是忍耐和坚持，只有懦弱和失败者才会到处找借口",
            platform="douyin",
            content_type="video",
        ),
        _simple_capture(
            workspace_dir,
            "recenttxt002",
            created_at="2026-04-11 09:57:00",
            title="一个人变强的过程，就是忍受痛苦",
            platform="xiaohongshu",
            content_type="image_article",
        ),
        _simple_capture(
            workspace_dir,
            "recenttxt003",
            created_at="2026-04-11 09:56:00",
            title="宿命论是一面镜子，照见的从来不是真相，而是照镜子的人",
            platform="wechat_article",
            content_type="article",
        ),
        _simple_capture(
            workspace_dir,
            "recenttxt004",
            created_at="2026-04-11 09:55:30",
            title="你只能靠自己得到自己想要的东西",
            platform="youtube",
            content_type="video",
        ),
        _failed_capture("recentbad001", created_at="2026-04-11 09:55:00"),
        _legacy_platform_failure_capture(
            "recentlegacybad001", created_at="2026-04-11 09:54:50"
        ),
        _permanent_failure_capture(
            "recentpermanent001", created_at="2026-04-11 09:54:48"
        ),
        _transient_connection_failure_capture(
            "recenttransient001", created_at="2026-04-11 09:54:42"
        ),
        _retry_exhausted_capture(
            "recentretryexhausted001", created_at="2026-04-11 09:54:40"
        ),
        _transcription_runtime_unavailable_capture(
            "recenttranscribefixture001", created_at="2026-04-11 09:54:35"
        ),
        _xiaohongshu_short_link_capture("recentshort001", created_at="2026-04-11 09:54:45"),
        _processing_capture("demoprocessing001", created_at="2026-04-11 09:54:30"),
    ]

    for capture in captures:
        _seed_capture(workspace_dir, capture)


class UISmokeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = _project_root()
        if not (cls.project_root / "frontend" / "dist" / "index.html").exists():
            raise unittest.SkipTest("frontend/dist is missing. Run the frontend build before UI smoke.")

        browsers_dir = cls.project_root / "workspace" / "runtime" / "playwright-browsers"
        if not browsers_dir.exists():
            raise unittest.SkipTest("Project Playwright browser runtime is missing.")

        cls.tempdir = tempfile.TemporaryDirectory()
        cls.workspace_dir = Path(cls.tempdir.name) / "workspace"
        _seed_workspace(cls.workspace_dir)
        cls.server_log_path = Path(cls.tempdir.name) / "ui-smoke-server.log"
        cls.server_log = cls.server_log_path.open("w", encoding="utf-8")

        cls.port = _free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
        env = os.environ.copy()
        env.update(
            {
                "AUDIO2TEXT_WORKSPACE_DIR": str(cls.workspace_dir),
                # UI fixtures intentionally exercise retry, challenge and
                # legacy-recovery paths. Keep their diagnostics beside the
                # disposable workspace so a direct UI-smoke run cannot make
                # production workspace/logs look like a real user failure.
                "AUDIO2TEXT_LOGS_DIR": str(Path(cls.tempdir.name) / "logs"),
                "AUDIO2TEXT_API_HOST": "127.0.0.1",
                "AUDIO2TEXT_API_PORT": str(cls.port),
                "AUDIO2TEXT_TRANSCRIPTION_PROVIDER": "openai_compatible",
                "AUDIO2TEXT_OPENAI_COMPATIBLE_BASE_URL": "https://example.com/v1",
                "AUDIO2TEXT_OPENAI_COMPATIBLE_API_KEY": "ui-smoke-key",
                "AUDIO2TEXT_OPENAI_COMPATIBLE_MODEL": "gpt-4o-mini-transcribe",
                "RELAY_AUTO_START_WORKER": "0",
                # Keep every seeded state available for deep-link assertions;
                # the production history limit should not prune test fixtures
                # before the browser reaches them.
                "CAPTURE_HISTORY_LIMIT": "32",
                "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir),
            }
        )
        cls.server_process = subprocess.Popen(
            [str(_python_executable()), "-m", "scripts.start_api"],
            cwd=str(cls.project_root),
            env=env,
            stdout=cls.server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            _wait_for_http(f"{cls.base_url}/health")
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:
            cls.server_log.flush()
            logs = ""
            try:
                logs = cls.server_log_path.read_text(encoding="utf-8")
            except Exception:  # pragma: no cover - best effort on startup failure
                logs = ""
            _terminate_process_tree(cls.server_process)
            cls.server_log.close()
            _cleanup_temporary_workspace(cls.tempdir)
            raise RuntimeError(f"UI smoke server failed to start: {exc}\n{logs}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "browser"):
            cls.browser.close()
        if hasattr(cls, "playwright"):
            cls.playwright.stop()
        if hasattr(cls, "server_process"):
            _terminate_process_tree(cls.server_process)
        if hasattr(cls, "server_log"):
            cls.server_log.close()
        if hasattr(cls, "tempdir"):
            _cleanup_temporary_workspace(cls.tempdir)

    def setUp(self) -> None:
        _seed_workspace(self.workspace_dir)
        self.context = self.browser.new_context(viewport={"width": 1512, "height": 940})
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.page.set_default_navigation_timeout(30000)

    def tearDown(self) -> None:
        try:
            self.page.evaluate(
                """() => {
                    document.querySelectorAll("video").forEach((video) => {
                      video.pause();
                      video.removeAttribute("src");
                      video.load();
                    });
                  }"""
            )
            self.page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
        except Exception:
            pass
        self.page.close()
        self.context.close()

    def test_homepage_recent_history_is_stable(self) -> None:
        self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
        self.page.locator("textarea").wait_for()
        self.assertTrue(self.page.locator("textarea").is_visible())
        self.page.locator(".recent-inline-card").first.wait_for()
        self.assertEqual(self.page.locator(".recent-inline-card").count(), 4)
        self.assertGreaterEqual(self.page.locator(".recent-inline-preview").count(), 1)
        self.assertGreaterEqual(self.page.locator(".recent-inline-foot").count(), 4)

        heights = []
        for index in range(self.page.locator(".recent-inline-card").count()):
            box = self.page.locator(".recent-inline-card").nth(index).bounding_box()
            self.assertIsNotNone(box)
            heights.append(round(box["height"], 2))

        self.assertLessEqual(max(heights) - min(heights), 2.0)

        self.page.get_by_role("button", name="查看更多").click()
        self.page.wait_for_timeout(200)
        self.assertTrue(self.page.get_by_role("button", name="收起").is_visible())
        self.assertGreaterEqual(self.page.locator(".recent-inline-card").count(), 5)

        first_shell = self.page.locator(".recent-inline-card-shell").first
        first_title = first_shell.locator(".recent-inline-card strong").inner_text()
        first_shell.hover()
        first_shell.locator(".recent-inline-delete").click(force=True)
        toast = self.page.locator(".toast.is-actionable").filter(has_text="已删除这条记录")
        toast.wait_for()
        self.assertTrue(toast.is_visible())
        self.page.get_by_role("button", name="撤销").click()
        self.page.wait_for_timeout(200)
        self.assertTrue(self.page.locator(".recent-inline-card").filter(has_text=first_title).first.is_visible())

    def test_client_input_error_stays_on_home_surface(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
        self.page.locator("textarea").fill("http://127.0.0.1:9/nope")
        self.page.get_by_role("button", name="开始处理").click()

        self.page.get_by_text("不能处理本机或内网地址，请提供公开网页链接。", exact=True).wait_for()
        self.page.locator(".input-stage").wait_for(state="visible")
        self.assertTrue(self.page.locator(".input-stage").is_visible())
        self.assertEqual(self.page.locator(".processing-stage").count(), 0)
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)

    def test_homepage_adapts_without_scaling_the_interface(self) -> None:
        expectations = [
            (1512, 940, 56.0, 4),
            (1280, 820, 53.76, 4),
            (760, 680, 32.0, 2),
            (390, 844, 32.0, 1),
        ]

        for width, height, expected_heading_size, expected_columns in expectations:
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
                self.page.locator("textarea").wait_for()
                self.page.wait_for_timeout(700)

                self.assertLessEqual(
                    self.page.evaluate("document.documentElement.scrollWidth"),
                    width,
                )
                heading_size = self.page.locator(".hero-copy h1").evaluate(
                    "element => parseFloat(getComputedStyle(element).fontSize)"
                )
                self.assertAlmostEqual(heading_size, expected_heading_size, delta=0.1)

                cards = self.page.locator(".recent-inline-card")
                cards.first.wait_for()
                first_row_top = cards.first.bounding_box()["y"]
                first_row_count = sum(
                    1
                    for index in range(cards.count())
                    if abs(cards.nth(index).bounding_box()["y"] - first_row_top) < 2
                )
                self.assertEqual(first_row_count, expected_columns)

    def test_release_viewports_have_no_web_download_or_fixed_result_panel(self) -> None:
        for width, height in ((390, 844), (768, 1024), (1440, 900), (2560, 1600)):
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(f"{self.base_url}/c/demoimage001", wait_until="domcontentloaded")
                self.page.locator(".deliverable-workspace").wait_for()
                self.assertEqual(self.page.get_by_text("Windows 版", exact=True).count(), 0)
                self.assertEqual(self.page.locator(".has-fixed-media").count(), 0)
                self.assertLessEqual(
                    self.page.evaluate("document.documentElement.scrollWidth"),
                    width + 2,
                )
                workspace = self.page.locator(".deliverable-workspace").bounding_box()
                self.assertIsNotNone(workspace)
                self.assertGreater(workspace["width"], min(350, width - 24))

    def test_result_workspace_preserves_centered_web_canvas(self) -> None:
        # Native IPC only supplies this disposable test server, never the installed app.
        for native in (False, True):
            context = self.browser.new_context()
            if native:
                context.add_init_script(
                    """window.__TAURI_INTERNALS__ = {
                        invoke: async (command) => {
                            if (command === "desktop_runtime_info") {
                                return {api_base: location.origin, token: ""};
                            }
                            throw new Error("Unexpected desktop command: " + command);
                        }
                    };"""
                )
            page = context.new_page()
            try:
                for width, height in ((1280, 820), (1707, 1045), (1920, 1080), (2560, 1600)):
                    for capture_id in ("demovideo001", "demoimagelong001"):
                        with self.subTest(native=native, viewport=f"{width}x{height}", capture=capture_id):
                            page.set_viewport_size({"width": width, "height": height})
                            page.goto(f"{self.base_url}/c/{capture_id}", wait_until="domcontentloaded")
                            page.locator(".deliverable-workspace").wait_for()
                            page.wait_for_function(
                                "document.querySelector('.stage-shell-ready')?.style.opacity === '1'"
                            )
                            metrics = page.evaluate(
                                """() => {
                                    const box = selector => {
                                        const r = document.querySelector(selector).getBoundingClientRect();
                                        return {x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom};
                                    };
                                    return {
                                        workspace: box(".deliverable-workspace"),
                                        primary: box(".deliverable-primary"),
                                        secondary: box(".media-panel, .image-gallery"),
                                        facts: box(".facts-strip"),
                                        overflow: document.documentElement.scrollWidth,
                                        native: !!document.querySelector(".is-native-chrome-only")
                                    };
                                }"""
                            )
                            self.assertEqual(metrics["native"], native)
                            self.assertLessEqual(metrics["overflow"], width + 2)
                            self.assertAlmostEqual(metrics["workspace"]["width"], 980, delta=2)
                            self.assertAlmostEqual(metrics["workspace"]["x"], (width - 980) / 2, delta=2)
                            self.assertLessEqual(abs(metrics["primary"]["height"] - metrics["secondary"]["height"]), 2)
                            self.assertGreater(metrics["primary"]["width"], metrics["workspace"]["width"] * 0.45)
                            self.assertLessEqual(metrics["primary"]["height"] / metrics["primary"]["width"], 1.35)
                            self.assertLessEqual(metrics["facts"]["bottom"], height + 1)
                            self.assertLessEqual(
                                metrics["facts"]["y"] - (metrics["workspace"]["y"] + metrics["workspace"]["height"]),
                                20,
                            )
                            screenshots = os.environ.get("UI_LAYOUT_SCREENSHOTS")
                            if screenshots and capture_id == "demovideo001":
                                destination = Path(screenshots)
                                destination.mkdir(parents=True, exist_ok=True)
                                page.screenshot(path=str(destination / f"{'native' if native else 'web'}-{width}x{height}.png"))
            finally:
                context.close()

    def test_mobile_deep_link_uses_full_result_width(self) -> None:
        self.context.close()
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.page.goto(f"{self.base_url}/c/demoimage001", wait_until="domcontentloaded")
        self.page.locator(".deliverable-workspace").wait_for()
        self.page.wait_for_timeout(700)

        self.assertEqual(self.page.locator(".main-stage > .stage-shell").count(), 1)
        stage_box = self.page.locator(".stage-shell-ready").bounding_box()
        self.assertIsNotNone(stage_box)
        self.assertGreaterEqual(stage_box["width"], 360)
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)

    def test_submit_flow_recovers_from_transient_capture_read(self) -> None:
        intercepted = {"count": 0}

        def stable_capture_create(route) -> None:
            if route.request.method == "POST":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"capture_id":"demoimage001","reused":true,"input_warning":null}',
                )
                return
            route.continue_()

        def flaky_capture_read(route) -> None:
            request = route.request
            if (
                request.method == "GET"
                and intercepted["count"] == 0
                and "/v1/captures/" in request.url
                and "/events" not in request.url
                and "/artifacts/" not in request.url
            ):
                intercepted["count"] += 1
                route.fulfill(status=503, content_type="application/json", body='{"detail":"服务暂时繁忙，请稍后重试。"}')
                return
            route.continue_()

        self.page.route("**/v1/captures", stable_capture_create)
        self.page.route("**/v1/captures/*", flaky_capture_read)
        self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
        self.page.locator("textarea").wait_for()
        self.page.locator("textarea").fill(
            "https://www.xiaohongshu.com/discovery/item/66f61f96000000001d03f12d"
        )
        self.page.locator(".hero-submit").click()

        self.page.wait_for_url("**/c/**", timeout=20000)
        self.page.locator(".image-card").first.wait_for(timeout=15000)
        self.assertEqual(intercepted["count"], 1)
        self.assertFalse(self.page.get_by_text("这条内容暂时没能处理成功").is_visible())
        self.assertIn("/c/", self.page.url)
        self.page.unroute("**/v1/captures/*", flaky_capture_read)
        self.page.unroute("**/v1/captures", stable_capture_create)

    def test_video_result_layout_stays_stable(self) -> None:
        self.page.goto(f"{self.base_url}/c/demovideo001", wait_until="domcontentloaded", timeout=60000)
        self.page.locator(".result-content-shell").wait_for()

        hero_metrics = self.page.locator(".deliverable-hero h1").evaluate(
            """el => {
                const style = getComputedStyle(el);
                return {
                  height: el.getBoundingClientRect().height,
                  lineHeight: parseFloat(style.lineHeight),
                  lineClamp: style.webkitLineClamp,
                  overflow: style.overflow,
                  title: el.getAttribute("title") || ""
                };
              }"""
        )
        self.assertEqual(hero_metrics["lineClamp"], "2")
        self.assertLessEqual(hero_metrics["height"], hero_metrics["lineHeight"] * 2 + 3)
        self.assertEqual(hero_metrics["overflow"], "hidden")
        self.assertTrue(hero_metrics["title"])

        shell_metrics = self.page.locator(".result-content-shell").evaluate(
            """el => {
                const style = getComputedStyle(el);
                const before = style.borderTopLeftRadius;
                el.scrollTop = 160;
                return {
                  clientHeight: el.clientHeight,
                  scrollHeight: el.scrollHeight,
                  overflowY: style.overflowY,
                  borderRadiusBefore: before,
                  borderRadiusAfter: getComputedStyle(el).borderTopLeftRadius
                };
              }"""
        )
        self.assertGreater(shell_metrics["scrollHeight"], shell_metrics["clientHeight"])
        self.assertIn(shell_metrics["overflowY"], {"auto", "scroll"})
        self.assertEqual(shell_metrics["borderRadiusBefore"], shell_metrics["borderRadiusAfter"])
        self.assertNotEqual(shell_metrics["borderRadiusAfter"], "0px")

        primary_box = self.page.locator(".result-surface").bounding_box()
        media_box = self.page.locator(".media-panel").bounding_box()
        workspace = self.page.locator(".deliverable-workspace")
        workspace_box = workspace.bounding_box()
        self.assertIsNotNone(primary_box)
        self.assertIsNotNone(media_box)
        self.assertIsNotNone(workspace_box)
        self.assertLessEqual(abs(primary_box["height"] - media_box["height"]), 16)
        self.assertGreater(workspace_box["height"], 460)
        self.assertFalse(
            self.page.locator(".deliverable-workspace-shell").evaluate(
                "el => el.classList.contains('has-fixed-media')"
            )
        )

        self.assertTrue(self.page.locator(".deliverable-hero .deliverable-meta").is_visible())
        facts = self.page.locator(".facts-strip")
        self.assertTrue(facts.is_visible())
        facts_box = facts.bounding_box()
        self.assertIsNotNone(facts_box)
        self.assertLessEqual(
            facts_box["y"] - (workspace_box["y"] + workspace_box["height"]),
            16,
        )
        self.assertLessEqual(facts_box["y"] + facts_box["height"], 901)
        source_action = self.page.get_by_role("button", name="查看来源")
        self.assertTrue(source_action.is_visible())
        source_box = source_action.bounding_box()
        self.assertIsNotNone(source_box)
        self.assertLessEqual(source_box["height"], 38)
        self.page.evaluate(
            "window.__openedSource = ''; window.open = url => { window.__openedSource = String(url); return null; }"
        )
        source_action.click()
        self.assertEqual(
            self.page.evaluate("window.__openedSource"),
            "https://example.com/demovideo001",
        )
        self.assertTrue(self.page.get_by_role("link", name="下载音频").is_visible())
        self.assertTrue(self.page.get_by_role("link", name="下载视频").is_visible())
        video = self.page.locator(".media-panel video")
        video.wait_for()
        self.page.wait_for_function("video => video.readyState >= 2", arg=video.element_handle())
        self.assertGreaterEqual(video.evaluate("element => element.readyState"), 2)

        button = self.page.locator(".result-actions .copy-action").first
        before_hover = button.evaluate(
            "el => ({borderColor: getComputedStyle(el).borderTopColor, color: getComputedStyle(el).color})"
        )
        # Verify copy-action buttons are present and styled
        self.assertNotIn("0, 0, 0, 0", before_hover["borderColor"])
        self.assertNotEqual(before_hover["color"], "rgba(0, 0, 0, 0)")

        brand_button = self.page.locator("button.brand-lockup")
        self.assertEqual(brand_button.get_attribute("title"), "返回工作台")
        brand_transform = brand_button.evaluate("el => getComputedStyle(el).transform")
        brand_button.hover()
        self.page.wait_for_timeout(220)
        self.assertNotEqual(
            brand_transform,
            brand_button.evaluate("el => getComputedStyle(el).transform"),
        )
        brand_button.click()
        self.page.locator("textarea").wait_for()
        self.assertEqual(urlparse(self.page.url).path, "/")

    def test_text_ready_video_has_a_recovery_action(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(
            f"{self.base_url}/c/demovideotextonly001",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        self.page.locator(".media-panel-empty").wait_for()
        self.assertTrue(self.page.get_by_text("暂未拿到可预览视频", exact=True).is_visible())
        self.assertIn(
            "文本已整理完成，但视频文件暂未就绪",
            self.page.locator(".media-panel-empty-card").inner_text(),
        )
        self.assertTrue(self.page.get_by_role("button", name="重新获取视频").is_visible())
        self.assertFalse(self.page.get_by_text("处理失败", exact=True).is_visible())

    def test_audio_result_download_action_renders(self) -> None:
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(f"{self.base_url}/c/demoaudio001", wait_until="domcontentloaded", timeout=30000)
        self.page.locator(".result-content-shell").wait_for()

        audio_action = self.page.get_by_role("link", name="下载音频")
        self.assertTrue(audio_action.is_visible())
        self.assertIn("/artifacts/source_audio", audio_action.get_attribute("href") or "")
        self.assertEqual(self.page.get_by_role("link", name="下载视频").count(), 0)
        self.assertTrue(self.page.get_by_role("region", name="播客播放器").is_visible())
        self.assertTrue(self.page.get_by_role("button", name="播放", exact=True).is_visible())
        self.assertTrue(self.page.get_by_role("slider", name="播放进度").is_visible())
        self.assertTrue(self.page.get_by_role("combobox", name="播放速度").is_visible())
        self.assertEqual(self.page.locator(".transcript-segment").count(), 8)
        self.assertEqual(self.page.get_by_role("button", name="处理新内容").count(), 0)
        brand_button = self.page.locator("button.brand-lockup")
        self.assertTrue(brand_button.is_visible())
        self.assertEqual(brand_button.get_attribute("title"), "返回工作台")

        self.page.get_by_role("button", name="播放", exact=True).click()
        self.page.evaluate(
            "window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'})"
        )
        self.page.locator(".podcast-compact-bar.is-visible").wait_for()
        compact_control = self.page.locator(".podcast-compact-bar button")
        self.assertTrue(compact_control.is_visible())
        self.assertIn(
            compact_control.get_attribute("aria-label"),
            {"暂停播客", "继续播放播客"},
        )
        self.page.get_by_role("region", name="播客播放器").scroll_into_view_if_needed()
        self.page.wait_for_function(
            "() => !document.querySelector('.podcast-compact-bar')?.classList.contains('is-visible')"
        )

    def test_podcast_panel_hugs_its_content_on_desktop(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/demoaudio001", wait_until="domcontentloaded", timeout=30000)
        panel = self.page.locator(".podcast-panel")
        footer = self.page.locator(".podcast-footer")
        panel.wait_for()

        panel_box = panel.bounding_box()
        footer_box = footer.bounding_box()
        text_box = self.page.locator(".result-surface").bounding_box()
        self.assertIsNotNone(panel_box)
        self.assertIsNotNone(footer_box)
        self.assertIsNotNone(text_box)
        self.assertLessEqual(
            panel_box["y"] + panel_box["height"] - (footer_box["y"] + footer_box["height"]),
            32,
        )
        self.assertLess(panel_box["height"], text_box["height"])
        self.assertEqual(self.page.locator(".facts-strip").count(), 1)

    def test_image_result_actions_render(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/demoimage001", wait_until="domcontentloaded")
        self.page.locator(".image-card").first.wait_for()
        self.page.locator(".image-card[aria-busy='true']").first.wait_for(state="detached", timeout=5000)

        self.assertTrue(self.page.locator(".image-card").first.is_visible())
        self.assertEqual(self.page.locator(".image-card[aria-busy='true']").count(), 0)
        self.assertEqual(
            self.page.locator(".image-card-media-image").evaluate_all(
                "els => els.filter(el => getComputedStyle(el).opacity === '0').length"
            ),
            0,
        )
        self.assertEqual(self.page.locator(".artifact-save-more").count(), 0)
        self.assertEqual(self.page.locator(".image-card-view-overlay").count(), 0)
        self.assertGreater(self.page.locator(".image-card-view-hint").count(), 0)
        primary_box = self.page.locator(".deliverable-primary").bounding_box()
        gallery_box = self.page.locator(".image-gallery").bounding_box()
        facts_box = self.page.locator(".facts-strip").bounding_box()
        workspace_box = self.page.locator(".deliverable-workspace").bounding_box()
        self.assertIsNotNone(primary_box)
        self.assertIsNotNone(gallery_box)
        self.assertIsNotNone(facts_box)
        self.assertIsNotNone(workspace_box)
        self.assertLessEqual(abs(primary_box["height"] - gallery_box["height"]), 2)
        self.assertGreater(workspace_box["height"], 460)
        self.assertGreaterEqual(
            facts_box["y"],
            workspace_box["y"] + workspace_box["height"] - 1,
        )
        source_action = self.page.get_by_role("button", name="查看来源")
        platform_chip = self.page.locator(".fact-chip-platform")
        self.assertTrue(source_action.is_visible())
        self.assertTrue(platform_chip.is_visible())
        self.assertEqual(
            source_action.evaluate("el => getComputedStyle(el).fontSize"),
            platform_chip.evaluate("el => getComputedStyle(el).fontSize"),
        )
        self.assertEqual(
            source_action.evaluate("el => getComputedStyle(el).lineHeight"),
            platform_chip.evaluate("el => getComputedStyle(el).lineHeight"),
        )
        footer_text = self.page.locator(".image-card-footer").first.inner_text()
        self.assertNotIn("Live", footer_text)
        self.assertNotIn("KB", footer_text)
        first_image_trigger = self.page.locator(".image-card .image-card-media-button").first
        first_image_trigger.click()
        self.page.locator(".image-viewer-overlay").wait_for()
        scroll_lock = self.page.evaluate(
            """() => ({
              htmlOverflow: getComputedStyle(document.documentElement).overflow,
              bodyOverflow: getComputedStyle(document.body).overflow
            })"""
        )
        self.assertEqual(scroll_lock["htmlOverflow"], "hidden")
        self.assertEqual(scroll_lock["bodyOverflow"], "hidden")
        self.assertEqual(self.page.locator(".image-viewer-embla-viewport").count(), 0)
        self.assertEqual(self.page.locator(".image-viewer-transition-underlay").count(), 0)
        self.assertTrue(self.page.locator(".image-viewer-close").is_visible())
        self.assertEqual(self.page.locator(".image-viewer-counter").count(), 0)
        self.assertTrue(self.page.locator(".image-viewer-toolbar").is_visible())
        self.assertTrue(self.page.locator(".image-viewer-stage video").is_visible())
        self.assertIn("1/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.assertEqual(self.page.locator(".image-viewer-toolbar").evaluate("el => getComputedStyle(el).opacity"), "1")
        self.page.locator(".image-viewer-media-frame.is-live").hover()
        self.page.wait_for_timeout(120)
        self.assertEqual(self.page.locator(".image-viewer-toolbar").evaluate("el => getComputedStyle(el).opacity"), "1")
        self.assertEqual(self.page.get_by_text("适应屏幕").count(), 0)
        self.assertEqual(self.page.get_by_text("原始尺寸").count(), 0)
        live_metrics = self.page.locator(".image-viewer-media-frame.is-live").evaluate(
            """el => {
                const video = el.querySelector("video");
                const frame = el.getBoundingClientRect();
                const videoBox = video.getBoundingClientRect();
                return {
                  frameWidth: frame.width,
                  videoWidth: videoBox.width,
                  frameHeight: frame.height,
                  videoHeight: videoBox.height,
                  videoRatio: video.videoWidth && video.videoHeight
                    ? video.videoWidth / video.videoHeight
                    : 0,
                  frameRatio: frame.width / frame.height,
                  frameBg: getComputedStyle(el).backgroundColor,
                  containerBg: getComputedStyle(el.closest('.yarl__container')).backgroundColor,
                  rootBg: getComputedStyle(el.closest('.yarl__root')).backgroundColor,
                  slideBg: getComputedStyle(el.closest('.yarl__slide')).backgroundColor,
                  wrapperBg: getComputedStyle(el.closest('.yarl__slide_wrapper')).backgroundColor
                };
              }"""
        )
        self.assertLessEqual(abs(live_metrics["frameWidth"] - live_metrics["videoWidth"]), 2)
        self.assertLessEqual(abs(live_metrics["frameHeight"] - live_metrics["videoHeight"]), 2)
        if live_metrics["videoRatio"] > 0:
            self.assertLessEqual(abs(live_metrics["frameRatio"] - live_metrics["videoRatio"]), 0.03)
        else:
            # The smoke fixture uses placeholder Live Photo bytes without video metadata;
            # the component's stable 3:4 fallback must still avoid a forced landscape frame.
            self.assertLessEqual(abs(live_metrics["frameRatio"] - 0.75), 0.03)
        self.assertNotIn("5, 5, 5", live_metrics["frameBg"])
        for background_name in ("frameBg", "containerBg", "rootBg", "slideBg", "wrapperBg"):
            self.assertNotIn("rgb(0, 0, 0)", live_metrics[background_name])
            self.assertNotIn("rgb(5, 5, 5)", live_metrics[background_name])
        nav_metrics = self.page.locator(".image-viewer-overlay").evaluate(
            """el => {
                const prev = el.querySelector(".image-viewer-nav.is-prev");
                const next = el.querySelector(".image-viewer-nav.is-next");
                const prevBox = prev.getBoundingClientRect();
                const nextBox = next.getBoundingClientRect();
                return {
                  prevCenter: prevBox.left + prevBox.width / 2,
                  nextCenter: nextBox.left + nextBox.width / 2,
                  prevSize: Math.min(prevBox.width, prevBox.height),
                  nextSize: Math.min(nextBox.width, nextBox.height),
                  viewportWidth: window.innerWidth
                };
              }"""
        )
        self.assertGreaterEqual(nav_metrics["prevSize"], 44)
        self.assertGreaterEqual(nav_metrics["nextSize"], 44)
        self.assertGreater(nav_metrics["prevCenter"], 0)
        self.assertLess(nav_metrics["nextCenter"], nav_metrics["viewportWidth"])
        self.page.mouse.wheel(0, 520)
        self.page.wait_for_timeout(420)
        self.assertIn("1/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.locator(".image-viewer-nav.is-next").click(force=True)
        self.page.wait_for_timeout(180)
        self.assertIn("2/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.assertEqual(self.page.locator(".image-viewer-transition-underlay").count(), 0)
        fade_metrics = self.page.locator(".yarl__slide_current .image-viewer-stage").evaluate(
            "el => ({animationName: getComputedStyle(el).animationName, animationDuration: getComputedStyle(el).animationDuration})"
        )
        self.assertEqual(fade_metrics["animationName"], "none")
        self.page.locator(".image-viewer-nav.is-prev").click(force=True)
        self.page.wait_for_timeout(180)
        self.assertIn("1/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.get_by_role("button", name="下一张", exact=True).click(force=True)
        self.page.wait_for_timeout(180)
        self.assertIn("2/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.get_by_role("button", name="上一张", exact=True).click(force=True)
        self.page.wait_for_timeout(180)
        self.assertIn("1/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.get_by_role("button", name="图片", exact=True).click()
        self.page.wait_for_timeout(120)
        self.assertTrue(self.page.locator(".yarl__slide_current .image-viewer-image").is_visible())
        self.assertTrue(self.page.get_by_role("button", name="旋转图片").is_visible())
        unzoomed_surface = self.page.locator(".yarl__slide_current .image-viewer-stage")
        unzoomed_box = unzoomed_surface.bounding_box()
        self.assertIsNotNone(unzoomed_box)
        self.page.mouse.move(
            unzoomed_box["x"] + unzoomed_box["width"] * 0.72,
            unzoomed_box["y"] + unzoomed_box["height"] / 2,
        )
        self.page.mouse.down()
        self.page.mouse.move(
            unzoomed_box["x"] + unzoomed_box["width"] * 0.18,
            unzoomed_box["y"] + unzoomed_box["height"] / 2,
            steps=12,
        )
        self.page.mouse.up()
        self.page.wait_for_timeout(420)
        self.assertIn("2/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.get_by_role("button", name="上一张", exact=True).click(force=True)
        self.page.wait_for_timeout(180)
        self.assertIn("1/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.locator(".image-viewer-toolbar").wait_for(state="visible")
        self.page.get_by_role("button", name="放大图片", exact=True).wait_for(state="visible")
        image_surface = self.page.locator(".yarl__slide_current .image-viewer-image-surface")
        zoom_surface = self.page.locator(".yarl__slide_current .yarl__slide_wrapper")
        before_zoom_transform = zoom_surface.evaluate("el => getComputedStyle(el).transform")
        self.page.get_by_role("button", name="放大图片").click()
        self.page.get_by_role("button", name="放大图片").click()
        self.page.wait_for_timeout(420)
        after_zoom_transform = zoom_surface.evaluate("el => getComputedStyle(el).transform")
        self.assertNotEqual(before_zoom_transform, after_zoom_transform)
        surface_box = image_surface.bounding_box()
        self.assertIsNotNone(surface_box)
        self.page.mouse.move(surface_box["x"] + surface_box["width"] / 2, surface_box["y"] + surface_box["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(surface_box["x"] + surface_box["width"] / 2 + 42, surface_box["y"] + surface_box["height"] / 2 + 18)
        self.page.mouse.up()
        self.page.wait_for_timeout(80)
        after_drag_transform = zoom_surface.evaluate("el => getComputedStyle(el).transform")
        self.assertNotEqual(after_zoom_transform, after_drag_transform)
        self.page.get_by_role("button", name="旋转图片").click()
        self.page.wait_for_timeout(80)
        after_rotate_transform = image_surface.evaluate("el => getComputedStyle(el).transform")
        self.assertNotEqual("none", after_rotate_transform)
        self.page.get_by_role("button", name="关闭图片预览").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator(".image-viewer-overlay").count(), 0)
        self.assertTrue(first_image_trigger.evaluate("el => document.activeElement === el"))

        first_image_trigger.click()
        self.page.locator(".image-viewer-overlay").wait_for()
        self.page.get_by_role("button", name="Live", exact=True).click()
        self.page.wait_for_timeout(150)
        self.assertTrue(self.page.locator(".image-viewer-stage video").is_visible())

        self.page.get_by_role("button", name="关闭图片预览").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator(".image-viewer-overlay").count(), 0)
        self.assertGreater(self.page.locator(".image-card a[href]").count(), 0)

    def test_processing_uses_one_quiet_loading_indicator(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/demoprocessing001", wait_until="domcontentloaded")
        self.page.locator(".processing-stage").wait_for()
        self.page.wait_for_function(
            "() => document.querySelector('.processing-meter')?.innerText.includes('52%')",
            timeout=30000,
        )

        self.assertEqual(self.page.locator(".processing-indicator").count(), 1)
        self.assertEqual(self.page.locator(".icon-spin").count(), 0)
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)
        meter_text = self.page.locator(".processing-meter").inner_text()
        self.assertIn("52%", meter_text)
        self.assertNotRegex(meter_text, r"失败|出错|异常|错误|报错|provider timeout")
        # The removed mascot used to appear only after a delayed timer. Sample
        # the page well past that old transition window so this regression
        # cannot pass by checking only the first paint.
        for delay in (1250, 3000, 5000):
            self.page.wait_for_timeout(delay)
            self.assertEqual(self.page.locator(".processing-indicator").count(), 1)
            self.assertEqual(self.page.locator(".icon-spin").count(), 0)
            self.assertEqual(self.page.locator(".processing-mascot").count(), 0)
        animation = self.page.locator(".processing-indicator-ring").evaluate(
            "el => getComputedStyle(el, '::after').animationName"
        )
        self.assertEqual(animation, "icon-spin")

        reduced_context = self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            reduced_motion="reduce",
        )
        reduced_page = reduced_context.new_page()
        reduced_page.set_default_timeout(15000)
        try:
            reduced_page.goto(
                f"{self.base_url}/c/demoprocessing001",
                wait_until="domcontentloaded",
            )
            self.assertEqual(
                reduced_page.locator(".processing-indicator-ring").evaluate(
                    "el => getComputedStyle(el, '::after').animationName"
                ),
                "none",
            )
        finally:
            reduced_context.close()

    def test_missing_deep_link_leaves_loading_state_with_a_quiet_recovery(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(
            f"{self.base_url}/c/does-not-exist-for-ui-smoke",
            wait_until="domcontentloaded",
        )
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertFalse(
            self.page.get_by_role("heading", name="正在恢复上一条内容").is_visible()
        )
        self.assertTrue(self.page.get_by_role("button", name="返回首页").is_visible())
        self.assertFalse(self.page.get_by_role("button", name="重新处理").is_visible())
        self.assertNotIn("Capture not found", self.page.locator(".processing-copy").inner_text())
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)

    def test_recoverable_platform_failure_has_no_legacy_mascot(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/recentbad001", wait_until="domcontentloaded")
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertEqual(self.page.locator(".processing-indicator").count(), 0)
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)
        self.assertTrue(self.page.get_by_role("heading", name="完成平台验证后继续").is_visible())
        self.assertTrue(self.page.get_by_role("button", name="打开平台完成验证").is_visible())
        self.assertTrue(self.page.get_by_role("button", name="完成后重新检查").is_visible())

    def test_legacy_platform_failure_is_rendered_as_recoverable_attention(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(
            f"{self.base_url}/c/recentlegacybad001",
            wait_until="domcontentloaded",
        )
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertEqual(self.page.locator(".processing-indicator").count(), 0)
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)
        self.assertTrue(self.page.get_by_role("heading", name="完成平台验证后继续").is_visible())
        self.assertTrue(self.page.get_by_role("button", name="打开平台完成验证").is_visible())

    def test_permanent_failure_has_one_quiet_exit_without_legacy_mascot(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(
            f"{self.base_url}/c/recentpermanent001",
            wait_until="domcontentloaded",
        )
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertEqual(self.page.locator(".processing-indicator").count(), 0)
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)
        self.assertTrue(
            self.page.get_by_role("heading", name="这条内容暂时不可用").is_visible()
        )
        self.assertTrue(self.page.get_by_text("内容暂不可用", exact=True).is_visible())
        self.assertFalse(self.page.get_by_text("处理失败", exact=True).is_visible())
        self.assertTrue(self.page.get_by_role("button", name="返回首页").is_visible())

    def test_transient_connection_failure_has_recovery_copy(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(
            f"{self.base_url}/c/recenttransient001",
            wait_until="domcontentloaded",
        )
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertTrue(self.page.get_by_role("heading", name="暂时连不上来源").is_visible())
        self.assertTrue(self.page.get_by_text("连接暂时不稳定", exact=True).is_visible())
        self.assertIn("当前网络环境没有稳定连上来源站点", self.page.locator(".processing-copy").inner_text())
        self.assertTrue(self.page.get_by_role("button", name="重新处理").is_visible())
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)

    def test_transcription_runtime_unavailable_has_specific_recovery_copy(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/recenttranscribefixture001", wait_until="domcontentloaded")
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertTrue(self.page.get_by_role("heading", name="转写组件还没有准备好").is_visible())
        self.assertTrue(self.page.get_by_text("转写组件未准备好", exact=True).is_visible())
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)

    def test_retry_exhausted_connection_stays_recoverable(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(
            f"{self.base_url}/c/recentretryexhausted001",
            wait_until="domcontentloaded",
        )
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertTrue(self.page.get_by_role("heading", name="暂时连不上来源").is_visible())
        self.assertTrue(self.page.get_by_text("连接暂时不稳定", exact=True).is_visible())
        self.assertTrue(self.page.get_by_role("button", name="重新处理").is_visible())
        self.assertFalse(self.page.get_by_text("处理失败", exact=True).is_visible())
        self.assertEqual(self.page.locator(".processing-mascot").count(), 0)

    def test_xiaohongshu_short_link_uses_complete_url_guidance(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/recentshort001", wait_until="domcontentloaded")
        self.page.locator(".processing-stage.is-attention").wait_for()
        self.assertEqual(self.page.locator(".processing-indicator").count(), 0)
        self.assertTrue(self.page.get_by_role("heading", name="请粘贴小红书完整链接").is_visible())
        self.assertIn("短链接没有稳定展开", self.page.locator(".processing-copy").inner_text())
        self.assertTrue(self.page.get_by_role("button", name="返回首页").is_visible())

    def test_image_result_matches_deployed_metadata_structure(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/demoimage001", wait_until="domcontentloaded")
        self.page.locator(".image-card").first.wait_for()

        self.assertEqual(self.page.locator(".deliverable-hero .deliverable-meta").count(), 1)
        self.assertEqual(self.page.locator(".facts-strip").count(), 1)
        self.assertEqual(self.page.locator("footer.deliverable-meta").count(), 0)

    def test_image_result_layout_does_not_depend_on_text_length(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(f"{self.base_url}/c/demoimagelong001", wait_until="domcontentloaded")
        self.page.locator(".image-card").first.wait_for()

        shell = self.page.locator(".deliverable-workspace-shell")
        workspace = self.page.locator(".deliverable-workspace")
        primary = self.page.locator(".deliverable-primary")
        gallery = self.page.locator(".image-gallery")
        metadata = self.page.locator(".facts-strip")
        shell_box = shell.bounding_box()
        workspace_box = workspace.bounding_box()
        primary_box = primary.bounding_box()
        gallery_box = gallery.bounding_box()
        metadata_box = metadata.bounding_box()

        self.assertIsNotNone(shell_box)
        self.assertIsNotNone(workspace_box)
        self.assertIsNotNone(primary_box)
        self.assertIsNotNone(gallery_box)
        self.assertIsNotNone(metadata_box)
        self.assertFalse(shell.evaluate("el => el.classList.contains('has-long-text')"))
        self.assertFalse(workspace.evaluate("el => el.classList.contains('has-long-text')"))
        self.assertLessEqual(abs(primary_box["height"] - gallery_box["height"]), 2)
        self.assertGreater(workspace_box["height"], 460)
        self.assertLessEqual(
            metadata_box["y"] - (workspace_box["y"] + workspace_box["height"]),
            16,
        )
        self.assertLessEqual(metadata_box["y"] + metadata_box["height"], 901)

    def test_tall_window_keeps_normal_result_proportions(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 1600})
        self.page.goto(f"{self.base_url}/c/demoimage001", wait_until="domcontentloaded")
        self.page.locator(".image-card").first.wait_for()

        shell = self.page.locator(".deliverable-workspace-shell")
        workspace = self.page.locator(".deliverable-workspace")
        workspace_box = workspace.bounding_box()

        self.assertIsNotNone(workspace_box)
        self.assertFalse(shell.evaluate("el => el.classList.contains('has-fixed-media')"))
        self.assertGreater(workspace_box["height"], 400)
        self.assertLessEqual(workspace_box["height"], 620)
        outer = self.page.locator('.deliverable-stage-inner').bounding_box()
        self.assertLessEqual(outer['height'], 820)
        self.assertAlmostEqual(outer['y'] + outer['height'] / 2, 72 + (1600 - 96) / 2, delta=3)

    def test_mobile_result_actions_and_viewer_are_tappable(self) -> None:
        context = self.browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
            device_scale_factor=3,
        )
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(30000)
        try:
            page.goto(f"{self.base_url}/c/demoimage001", wait_until="domcontentloaded")
            page.locator(".image-card").first.wait_for()
            layout = page.evaluate(
                """() => {
                    const width = window.innerWidth;
                    const docWidth = document.documentElement.scrollWidth;
                    const visibleTargets = [...document.querySelectorAll(".image-card-media-button, .image-card-action, .result-actions > *")]
                      .filter((el) => {
                        const box = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && style.display !== "none";
                      })
                      .map((el) => {
                        const box = el.getBoundingClientRect();
                        return {width: box.width, height: box.height, right: box.right};
                      });
                    return {
                      width,
                      docWidth,
                      minTargetHeight: Math.min(...visibleTargets.map((item) => item.height)),
                      maxRight: Math.max(...visibleTargets.map((item) => item.right)),
                    };
                  }"""
            )
            self.assertLessEqual(layout["docWidth"], layout["width"] + 2)
            self.assertGreaterEqual(layout["minTargetHeight"], 36)
            self.assertLessEqual(layout["maxRight"], layout["width"] + 2)

            page.locator(".image-card .image-card-media-button").first.click()
            page.locator(".image-viewer-overlay").wait_for()
            toolbar_metrics = page.locator(".image-viewer-toolbar").evaluate(
                """el => {
                    const box = el.getBoundingClientRect();
                    return {
                      left: box.left,
                      right: box.right,
                      height: box.height,
                      opacity: getComputedStyle(el).opacity,
                      buttonCount: el.querySelectorAll("button, a").length,
                      minButtonHeight: Math.min(...[...el.querySelectorAll("button, a")].map((item) => item.getBoundingClientRect().height)),
                      width: window.innerWidth
                    };
                  }"""
            )
            self.assertEqual(toolbar_metrics["opacity"], "1")
            self.assertGreaterEqual(toolbar_metrics["buttonCount"], 5)
            self.assertGreaterEqual(toolbar_metrics["minButtonHeight"], 44)
            self.assertGreaterEqual(toolbar_metrics["left"], -1)
            self.assertLessEqual(toolbar_metrics["right"], toolbar_metrics["width"] + 1)
            self.assertLessEqual(toolbar_metrics["height"], 190)

            page.locator(".yarl__container").evaluate(
                """el => {
                    const emit = (type, x, buttons) => el.dispatchEvent(new PointerEvent(type, {
                      bubbles: true,
                      cancelable: true,
                      pointerId: 7,
                      pointerType: "touch",
                      isPrimary: true,
                      buttons,
                      clientX: x,
                      clientY: 420,
                    }));
                    emit("pointerdown", 330, 1);
                    emit("pointermove", 240, 1);
                    emit("pointermove", 110, 1);
                    emit("pointerup", 110, 0);
                  }"""
            )
            page.wait_for_timeout(420)
            self.assertIn("2/2", page.locator(".image-viewer-toolbar-count").inner_text())
            page.locator(".image-viewer-close").click()
            page.wait_for_timeout(150)
            self.assertEqual(page.locator(".image-viewer-overlay").count(), 0)

            page.goto(f"{self.base_url}/c/demovideo001", wait_until="domcontentloaded")
            page.locator(".media-panel").wait_for()
            video_layout = page.evaluate(
                """() => ({
                    width: window.innerWidth,
                    docWidth: document.documentElement.scrollWidth,
                    actionMaxRight: Math.max(...[...document.querySelectorAll(".result-actions > *, .media-panel-actions > *")].map((el) => el.getBoundingClientRect().right)),
                    actionMinHeight: Math.min(...[...document.querySelectorAll(".result-actions > *, .media-panel-actions > *")].map((el) => el.getBoundingClientRect().height)),
                  })"""
            )
            self.assertLessEqual(video_layout["docWidth"], video_layout["width"] + 2)
            self.assertLessEqual(video_layout["actionMaxRight"], video_layout["width"] + 2)
            self.assertGreaterEqual(video_layout["actionMinHeight"], 43.5)
            self.assertTrue(page.get_by_role("link", name="下载音频").is_visible())
        finally:
            try:
                page.evaluate(
                    """() => {
                        document.querySelectorAll("video").forEach((video) => {
                          video.pause();
                          video.removeAttribute("src");
                          video.load();
                        });
                      }"""
                )
                page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
            except Exception:
                pass
            page.close()
            context.close()


if __name__ == "__main__":
    unittest.main()

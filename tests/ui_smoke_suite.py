from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from app.schemas import (
    ArtifactModel,
    CaptureModel,
    ProcessingStateModel,
    ResultDocumentModel,
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
            content=b"fake-preview-video",
        ),
        _artifact(
            workspace_dir,
            capture_id,
            "source_media",
            "source_media.mp4",
            mime_type="video/mp4",
            content=b"fake-source-video",
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


def _audio_capture(workspace_dir: Path, capture_id: str, *, created_at: str, title: str) -> CaptureModel:
    primary_text = "小宇宙音频样例已经转写完成。\n\n这里保留两段正文，用于验证音频下载入口不会和视频下载混在一起。"
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


def _image_capture(workspace_dir: Path, capture_id: str, *, created_at: str, title: str) -> CaptureModel:
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
            primary_text="",
            primary_result_type="article",
            confidence="high",
            completeness="full",
            transcript_status="skipped",
            text_source="article",
            views=ResultViewsModel(),
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
        status="failed",
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
        processing=ProcessingStateModel(current_stage="failed", progress_percent=100),
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
        _audio_capture(workspace_dir, "demoaudio001", created_at="2026-04-11 09:59:30", title="蚂蚁测试 AI 版支付宝，市场监管总局约谈山姆"),
        _image_capture(workspace_dir, "demoimage001", created_at="2026-04-11 09:59:00", title="图文整理结果样例"),
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
                "AUDIO2TEXT_API_HOST": "127.0.0.1",
                "AUDIO2TEXT_API_PORT": str(cls.port),
                "AUDIO2TEXT_TRANSCRIPTION_PROVIDER": "openai_compatible",
                "AUDIO2TEXT_OPENAI_COMPATIBLE_BASE_URL": "https://example.com/v1",
                "AUDIO2TEXT_OPENAI_COMPATIBLE_API_KEY": "ui-smoke-key",
                "AUDIO2TEXT_OPENAI_COMPATIBLE_MODEL": "gpt-4o-mini-transcribe",
                "RELAY_AUTO_START_WORKER": "0",
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
            cls.tempdir.cleanup()
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
            cls.tempdir.cleanup()

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
        self.assertIsNotNone(primary_box)
        self.assertIsNotNone(media_box)
        self.assertLessEqual(abs(primary_box["height"] - media_box["height"]), 16)

        meta_footer = self.page.locator(".deliverable-meta")
        self.assertTrue(meta_footer.is_visible())
        source_link = self.page.get_by_role("link", name="查看来源")
        self.assertTrue(source_link.is_visible())
        self.assertEqual(source_link.get_attribute("target"), "_blank")
        self.assertTrue(self.page.get_by_role("link", name="下载音频").is_visible())
        self.assertTrue(self.page.get_by_role("link", name="下载视频").is_visible())

        button = self.page.locator(".result-actions .copy-action").first
        before_hover = button.evaluate(
            "el => ({borderColor: getComputedStyle(el).borderTopColor, color: getComputedStyle(el).color})"
        )
        # Verify copy-action buttons are present and styled
        self.assertNotIn("0, 0, 0, 0", before_hover["borderColor"])
        self.assertNotEqual(before_hover["color"], "rgba(0, 0, 0, 0)")

    def test_audio_result_download_action_renders(self) -> None:
        self.page.goto(f"{self.base_url}/c/demoaudio001", wait_until="domcontentloaded", timeout=30000)
        self.page.locator(".result-content-shell").wait_for()

        audio_action = self.page.get_by_role("link", name="下载音频")
        self.assertTrue(audio_action.is_visible())
        self.assertIn("/artifacts/source_audio", audio_action.get_attribute("href") or "")
        self.assertEqual(self.page.get_by_role("link", name="下载视频").count(), 0)

    def test_image_result_actions_render(self) -> None:
        self.page.goto(f"{self.base_url}/c/demoimage001", wait_until="domcontentloaded")
        self.page.locator(".image-card").first.wait_for()

        self.assertTrue(self.page.locator(".image-card").first.is_visible())
        self.assertEqual(self.page.locator(".image-card-view-overlay").count(), 0)
        self.assertGreater(self.page.locator(".image-card-view-hint").count(), 0)
        footer_text = self.page.locator(".image-card-footer").first.inner_text()
        self.assertNotIn("Live", footer_text)
        self.assertNotIn("KB", footer_text)
        self.page.locator(".image-card .image-card-media-button").first.click()
        self.page.locator(".image-viewer-overlay").wait_for()
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
                  frameBg: getComputedStyle(el).backgroundColor
                };
              }"""
        )
        self.assertLessEqual(abs(live_metrics["frameWidth"] - live_metrics["videoWidth"]), 2)
        self.assertLessEqual(abs(live_metrics["frameHeight"] - live_metrics["videoHeight"]), 2)
        self.assertNotIn("248, 248, 244", live_metrics["frameBg"])
        nav_metrics = self.page.locator(".image-viewer-overlay").evaluate(
            """el => {
                const frame = el.querySelector(".image-viewer-media-frame");
                const prev = el.querySelector(".image-viewer-nav.is-prev");
                const next = el.querySelector(".image-viewer-nav.is-next");
                const frameBox = frame.getBoundingClientRect();
                const prevBox = prev.getBoundingClientRect();
                const nextBox = next.getBoundingClientRect();
                return {
                  frameLeft: frameBox.left,
                  frameRight: frameBox.right,
                  prevCenter: prevBox.left + prevBox.width / 2,
                  nextCenter: nextBox.left + nextBox.width / 2,
                  viewportWidth: window.innerWidth
                };
              }"""
        )
        self.assertGreater(nav_metrics["prevCenter"], nav_metrics["frameLeft"] - 16)
        self.assertLess(nav_metrics["prevCenter"], nav_metrics["frameLeft"] + 80)
        self.assertGreater(nav_metrics["nextCenter"], nav_metrics["frameRight"] - 80)
        self.assertLess(nav_metrics["nextCenter"], nav_metrics["frameRight"] + 16)
        self.page.mouse.wheel(0, 520)
        self.page.wait_for_timeout(420)
        self.assertIn("2/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.locator(".image-viewer-nav.is-prev").click(force=True)
        self.page.locator(".image-viewer-transition-underlay").wait_for(state="attached")
        self.assertGreaterEqual(self.page.locator(".image-viewer-transition-underlay").count(), 1)
        self.page.wait_for_timeout(180)
        self.assertIn("1/2", self.page.locator(".image-viewer-toolbar-count").inner_text())
        self.page.get_by_role("button", name="图片", exact=True).click()
        self.page.wait_for_timeout(120)
        self.assertTrue(self.page.locator(".image-viewer-image").is_visible())
        self.assertTrue(self.page.get_by_role("button", name="旋转图片").is_visible())
        image_surface = self.page.locator(".image-viewer-image-surface")
        before_zoom_transform = image_surface.evaluate("el => getComputedStyle(el).transform")
        self.page.get_by_role("button", name="放大图片").click()
        self.page.get_by_role("button", name="放大图片").click()
        self.page.wait_for_timeout(80)
        after_zoom_transform = image_surface.evaluate("el => getComputedStyle(el).transform")
        self.assertNotEqual(before_zoom_transform, after_zoom_transform)
        surface_box = image_surface.bounding_box()
        self.assertIsNotNone(surface_box)
        self.page.mouse.move(surface_box["x"] + surface_box["width"] / 2, surface_box["y"] + surface_box["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(surface_box["x"] + surface_box["width"] / 2 + 42, surface_box["y"] + surface_box["height"] / 2 + 18)
        self.page.mouse.up()
        self.page.wait_for_timeout(80)
        after_drag_transform = image_surface.evaluate("el => getComputedStyle(el).transform")
        self.assertNotEqual(after_zoom_transform, after_drag_transform)
        self.page.get_by_role("button", name="旋转图片").click()
        self.page.wait_for_timeout(80)
        after_rotate_transform = image_surface.evaluate("el => getComputedStyle(el).transform")
        self.assertNotEqual(after_drag_transform, after_rotate_transform)
        self.page.mouse.click(24, 180)
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator(".image-viewer-overlay").count(), 0)

        self.page.locator(".image-card .image-card-media-button").first.click()
        self.page.locator(".image-viewer-overlay").wait_for()
        self.page.get_by_role("button", name="Live", exact=True).click()
        self.page.wait_for_timeout(150)
        self.assertTrue(self.page.locator(".image-viewer-stage video").is_visible())

        self.page.get_by_role("button", name="关闭图片预览").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator(".image-viewer-overlay").count(), 0)
        self.assertGreater(self.page.locator(".image-card a[href]").count(), 0)

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

            page.locator(".image-viewer-overlay").evaluate(
                """el => {
                    const makeTouch = (x) => new Touch({identifier: 1, target: el, clientX: x, clientY: 420});
                    const start = makeTouch(330);
                    const end = makeTouch(110);
                    el.dispatchEvent(new TouchEvent("touchstart", {bubbles: true, cancelable: true, touches: [start], changedTouches: [start]}));
                    el.dispatchEvent(new TouchEvent("touchend", {bubbles: true, cancelable: true, touches: [], changedTouches: [end]}));
                  }"""
            )
            page.wait_for_timeout(180)
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

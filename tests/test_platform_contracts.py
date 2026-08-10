from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.pipeline as pipeline
import app.extractors as extractors
import app.source_adapters as source_adapters
from app.browser_provider import (
    BrowserProviderError,
    _douyin_chapter_text,
    _douyin_media_urls_from_detail,
    _pick_preferred_media_url,
)
from app.extractors import (
    ExtractionOutcome,
    _bilibili_best_dash_pair,
    _bilibili_progressive_media_url,
    _bilibili_subtitle_url,
    _classify_external_error,
    _extract_bilibili_video_id,
    _parse_web_subtitle,
    _select_subtitle_track,
    _select_youtube_transcript,
    _ytdlp_metadata,
    extract_xiaoyuzhou,
)
from app.settings import BROWSER_PROFILE_DIR
from app.main import _to_capture_envelope
from app.resolver import resolve_url
from app.schemas import ProcessingStateModel, ResultDocumentModel, SourceMetaModel
from scripts.run_transcribe import _normalize_requested_language, _should_convert_to_simplified


class _FallbackAdapter(source_adapters.BaseSourceAdapter):
    platform = "test_platform"

    def build_providers(self, resolved, capture_dir: Path, *, cookie_text: str = "", progress_callback=None):
        return [
            source_adapters.ProviderSpec(
                "direct_provider",
                lambda: (_ for _ in ()).throw(
                    source_adapters.ExtractionError("extract", "直连失败", reason_code="direct_failed", retryable=False)
                ),
            ),
            source_adapters.ProviderSpec(
                "open_source_provider",
                lambda: ExtractionOutcome(
                    platform="youtube",
                    content_type="video",
                    title="sample",
                    strategy=["metadata", "subtitle"],
                    subtitle_text="hello world",
                ),
            ),
        ]


class _BrowserFailureAdapter(source_adapters.BaseSourceAdapter):
    platform = "test_platform"

    def build_providers(self, resolved, capture_dir: Path, *, cookie_text: str = "", progress_callback=None):
        return [
            source_adapters.ProviderSpec(
                "browser_provider",
                lambda: (_ for _ in ()).throw(
                    BrowserProviderError(
                        "需要刷新项目级浏览器会话。",
                        reason_code="browser_session_expired",
                        retryable=True,
                    )
                ),
            )
        ]


class PlatformContractsTestCase(unittest.TestCase):
    def _xiaoyuzhou_episode_html(
        self,
        *,
        shownotes: str = "",
        episode_description: str = "",
        podcast_description: str = "",
        title: str = "Episode title",
        audio_url: str = "https://media.example.com/demo.m4a",
    ) -> str:
        next_data = {
            "props": {
                "pageProps": {
                    "episode": {
                        "title": title,
                        "description": episode_description,
                        "shownotes": shownotes,
                        "podcast": {
                            "description": podcast_description,
                        },
                    }
                }
            }
        }
        return f"""
        <html>
          <head>
            <title>{title}</title>
            <meta property="og:title" content="{title}" />
            <meta property="og:description" content="{podcast_description}" />
            <meta property="og:audio" content="{audio_url}" />
            <meta property="og:image" content="https://image.example.com/poster.jpg" />
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data, ensure_ascii=False)}</script>
          </head>
          <body>
            <article>
              <p>评论区占位，不应进入正文。</p>
            </article>
          </body>
        </html>
        """

    def test_resolve_url_routes_all_supported_platforms(self) -> None:
        cases = {
            "https://youtu.be/dQw4w9WgXcQ": "youtube",
            "https://www.bilibili.com/video/BV1xx411c7mD": "bilibili",
            "https://www.xiaoyuzhoufm.com/episode/67f0146e64f12bfc04dca0d1": "xiaoyuzhou",
            "https://www.douyin.com/video/7482735026417366324": "douyin",
            "https://www.xiaohongshu.com/explore/66b1e4b0000000001d00beef": "xiaohongshu",
            "https://mp.weixin.qq.com/s/example": "wechat_article",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                resolved = resolve_url(url)
                self.assertEqual(resolved.platform, expected)

    def test_bilibili_direct_contract_selects_subtitles_and_media(self) -> None:
        self.assertEqual(
            _extract_bilibili_video_id("https://www.bilibili.com/video/BV1xBjW6pEym/"),
            "BV1xBjW6pEym",
        )

        subtitle_url = _bilibili_subtitle_url(
            {
                "subtitle": {
                    "subtitles": [
                        {"subtitle_url": "//example.com/subtitle.json"},
                    ]
                }
            }
        )
        self.assertEqual(subtitle_url, "https://example.com/subtitle.json")

        progressive_url = _bilibili_progressive_media_url(
            {"durl": [{"url": "https://media.example.com/progressive.mp4"}]}
        )
        self.assertEqual(progressive_url, "https://media.example.com/progressive.mp4")

        video_url, audio_url = _bilibili_best_dash_pair(
            {
                "dash": {
                    "video": [
                        {
                            "baseUrl": "https://media.example.com/4k.m4s",
                            "height": 2160,
                            "bandwidth": 3000000,
                        },
                        {
                            "baseUrl": "https://media.example.com/720p.m4s",
                            "height": 720,
                            "bandwidth": 1500000,
                        },
                    ],
                    "audio": [
                        {
                            "baseUrl": "https://media.example.com/audio-low.m4s",
                            "bandwidth": 64000,
                        },
                        {
                            "baseUrl": "https://media.example.com/audio-high.m4s",
                            "bandwidth": 192000,
                        },
                    ],
                }
            }
        )
        self.assertEqual(video_url, "https://media.example.com/720p.m4s")
        self.assertEqual(audio_url, "https://media.example.com/audio-high.m4s")

    def test_douyin_provider_order_prefers_browser_before_open_source(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://www.douyin.com/video/1234567890")
        providers = source_adapters.DouyinAdapter().build_providers(resolved, Path(tempfile.gettempdir()))
        self.assertEqual(
            [provider.name for provider in providers],
            ["direct_provider", "browser_provider", "open_source_provider"],
        )

    def test_xiaohongshu_provider_order_excludes_generic_web_fallback(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://www.xiaohongshu.com/explore/demo")
        providers = source_adapters.XiaohongshuAdapter().build_providers(resolved, Path(tempfile.gettempdir()))
        self.assertEqual(
            [provider.name for provider in providers],
            ["browser_provider", "open_source_provider"],
        )

    def test_xiaohongshu_preserves_browser_challenge_error_priority(self) -> None:
        adapter = source_adapters.XiaohongshuAdapter()
        resolved = SimpleNamespace(normalized_url="https://www.xiaohongshu.com/explore/demo")

        with patch.object(
            adapter,
            "_browser_extract",
            side_effect=BrowserProviderError(
                "当前页面触发了平台验证，请刷新项目级浏览器会话后再试。",
                reason_code="browser_challenge_required",
                retryable=True,
            ),
        ), patch(
            "app.source_adapters.extract_with_ytdlp",
            side_effect=extractors.ExtractionError(
                "extract",
                "ERROR: [XiaoHongShu] No video formats found!",
                reason_code="extract_failed",
                retryable=False,
            ),
        ):
            with self.assertRaises(extractors.ExtractionError) as caught:
                adapter.extract(resolved, Path(tempfile.gettempdir()))

        self.assertEqual(caught.exception.reason_code, "browser_challenge_required")
        self.assertTrue(caught.exception.retryable)
        self.assertIn("平台验证", caught.exception.message)

    def test_adapter_success_records_extractor_and_fallback_metadata(self) -> None:
        adapter = _FallbackAdapter()
        resolved = SimpleNamespace(normalized_url="https://example.com/watch")

        with patch.object(source_adapters, "provider_is_available", return_value=True), patch.object(
            source_adapters, "record_provider_success", lambda *args, **kwargs: None
        ), patch.object(source_adapters, "record_provider_failure", lambda *args, **kwargs: None):
            outcome = adapter.extract(resolved, Path(tempfile.gettempdir()))

        self.assertEqual(outcome.extractor_used, "open_source_provider")
        self.assertTrue(outcome.fallback_used)
        self.assertEqual(outcome.fallback_chain, ["direct_provider", "open_source_provider"])
        self.assertGreaterEqual(len(outcome.provider_traces), 3)
        self.assertTrue(any("duration=" in (entry.detail or "") for entry in outcome.provider_traces))

    def test_douyin_direct_metadata_probe_has_bounded_timeout(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://www.douyin.com/video/1234567890123")

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            extractors,
            "_fetch_text",
            return_value=("https://www.douyin.com/video/1234567890123", ""),
        ), patch.object(extractors.time, "monotonic", side_effect=[0.0, 46.0]):
            with self.assertRaises(source_adapters.ExtractionError) as ctx:
                extractors.extract_douyin_direct(
                    resolved,
                    Path(tmp_dir),
                    metadata_timeout_seconds=45.0,
                )

        self.assertEqual(ctx.exception.reason_code, "douyin_direct_metadata_timeout")
        self.assertTrue(ctx.exception.retryable)

    def test_browser_provider_reason_code_is_preserved(self) -> None:
        adapter = _BrowserFailureAdapter()
        resolved = SimpleNamespace(normalized_url="https://example.com/watch")

        with patch.object(source_adapters, "provider_is_available", return_value=True), patch.object(
            source_adapters, "record_provider_success", lambda *args, **kwargs: None
        ), patch.object(source_adapters, "record_provider_failure", lambda *args, **kwargs: None):
            with self.assertRaises(source_adapters.ExtractionError) as ctx:
                adapter.extract(resolved, Path(tempfile.gettempdir()))

        self.assertEqual(ctx.exception.reason_code, "browser_session_expired")
        self.assertTrue(ctx.exception.retryable)
        self.assertTrue(any("浏览器会话" in warning for warning in ctx.exception.warnings))

    def test_youtube_subtitle_first_provider_can_enrich_media_artifact(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://youtu.be/demo")
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts"
        capture_dir.mkdir(parents=True, exist_ok=True)
        media_path = capture_dir / "demo.mp4"
        media_path.write_bytes(b"video")

        subtitle_outcome = ExtractionOutcome(
            platform="youtube",
            content_type="video",
            title="sample",
            subtitle_text="hello subtitle",
            strategy=["youtube_transcript_api", "subtitle"],
        )
        media_outcome = ExtractionOutcome(
            platform="youtube",
            content_type="video",
            title="sample",
            media_file_path=str(media_path),
            duration_seconds=12.0,
            thumbnail_url="https://example.com/poster.jpg",
            author="author",
            description="desc",
            strategy=["metadata", "media_download"],
        )

        with patch.object(source_adapters, "extract_youtube_transcript", return_value=subtitle_outcome), patch.object(
            source_adapters, "extract_with_ytdlp", return_value=media_outcome
        ):
            outcome = source_adapters.YouTubeAdapter().build_providers(resolved, capture_dir)[0].runner()

        self.assertEqual(outcome.media_file_path, str(media_path))
        self.assertIn("media_download", outcome.strategy)
        self.assertEqual(outcome.duration_seconds, 12.0)
        self.assertEqual(outcome.thumbnail_url, "https://example.com/poster.jpg")
        media_path.unlink(missing_ok=True)

    def test_ytdlp_subtitle_selection_prefers_original_language_manual_track(self) -> None:
        info = {
            "language": "en",
            "title": "English video",
            "description": "subtitle sample",
            "subtitles": {
                "en": [{"url": "https://example.com/en.vtt", "ext": "vtt"}],
                "zh-CN": [{"url": "https://example.com/zh.vtt", "ext": "vtt"}],
            },
            "automatic_captions": {
                "zh-CN": [{"url": "https://example.com/zh-auto.vtt", "ext": "vtt"}],
            },
        }

        track, subtitle_source, selected_language = _select_subtitle_track(info)

        self.assertIsNotNone(track)
        self.assertEqual(subtitle_source, "manual")
        self.assertEqual(selected_language, "en")

    def test_ytdlp_subtitle_selection_ignores_localized_page_metadata(self) -> None:
        info = {
            "language": "en",
            "title": "这是一个中文本地化标题",
            "description": "这是一个中文页面简介",
            "subtitles": {
                "en": [{"url": "https://example.com/en.vtt", "ext": "vtt"}],
                "zh-Hant": [{"url": "https://example.com/zh-hant.vtt", "ext": "vtt"}],
            },
        }

        track, subtitle_source, selected_language = _select_subtitle_track(info)

        self.assertIsNotNone(track)
        self.assertEqual(subtitle_source, "manual")
        self.assertEqual(selected_language, "en")

    def test_ytdlp_subtitle_selection_prefers_original_language_auto_before_translated_manual(self) -> None:
        info = {
            "language": "en",
            "title": "English video",
            "description": "subtitle sample",
            "subtitles": {
                "zh-CN": [{"url": "https://example.com/zh.vtt", "ext": "vtt"}],
            },
            "automatic_captions": {
                "en": [{"url": "https://example.com/en-auto.vtt", "ext": "vtt"}],
            },
        }

        track, subtitle_source, selected_language = _select_subtitle_track(info)

        self.assertIsNotNone(track)
        self.assertEqual(subtitle_source, "auto")
        self.assertEqual(selected_language, "en")

    def test_json3_subtitle_parser_extracts_text_without_payload_keys(self) -> None:
        raw = json.dumps(
            {
                "wireMagic": "pb3",
                "events": [
                    {"tStartMs": 18640, "segs": [{"utf8": "We're no strangers"}, {"utf8": " to love"}]},
                    {"tStartMs": 22640, "segs": [{"utf8": "You know the rules\nand so do I"}]},
                ],
            }
        )

        body, timeline = _parse_web_subtitle(raw)

        self.assertEqual(body, "We're no strangers to love\nYou know the rules\nand so do I")
        self.assertIn("00:18 We're no strangers to love", timeline)
        self.assertNotIn("wireMagic", body)
        self.assertNotIn("events", body)

    def test_youtube_transcript_selection_prefers_first_manual_track_without_page_language_bias(self) -> None:
        transcripts = [
            SimpleNamespace(language_code="en", is_generated=False),
            SimpleNamespace(language_code="zh-Hant", is_generated=False),
            SimpleNamespace(language_code="en", is_generated=True),
        ]

        selected = _select_youtube_transcript(transcripts)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.language_code, "en")
        self.assertFalse(selected.is_generated)

    def test_ytdlp_metadata_tries_multiple_browser_cookie_sources(self) -> None:
        seen_sources: list[tuple[str, ...] | None] = []

        class FakeYDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                browser_source = self.options.get("cookiesfrombrowser")
                seen_sources.append(browser_source)
                if browser_source == ("edge", str(BROWSER_PROFILE_DIR)):
                    raise Exception("Fresh cookies are needed")
                return {"id": "demo", "title": "sample"}

        with patch("app.extractors.YoutubeDL", FakeYDL):
            info, warnings = _ytdlp_metadata(
                "https://www.douyin.com/video/7618917735157806321",
                Path(tempfile.gettempdir()),
                platform="douyin",
            )

        self.assertEqual(info["id"], "demo")
        self.assertEqual(warnings, [])
        self.assertEqual(seen_sources, [("edge", str(BROWSER_PROFILE_DIR)), ("chrome", str(BROWSER_PROFILE_DIR))])

    def test_douyin_detail_urls_prefer_progressive_video_over_audio_streams(self) -> None:
        detail = {
            "video": {
                "play_addr_h264": {
                    "url_list": [
                        "https://media.example.com/video.mp4",
                        "https://media.example.com/video-backup.mp4",
                    ]
                },
                "bit_rate": [
                    {"play_addr": {"url_list": ["https://media.example.com/media-audio-und-mp4a"]}},
                ],
            }
        }

        self.assertEqual(
            _pick_preferred_media_url(_douyin_media_urls_from_detail(detail)),
            "https://media.example.com/video.mp4",
        )

    def test_xiaoyuzhou_shownotes_still_require_transcription(self) -> None:
        html = self._xiaoyuzhou_episode_html(
            shownotes=(
                "<p>第一段 shownotes，非常详细，足够长，能够直接作为交付文本。</p>"
                "<p>第二段 shownotes，继续补充重点信息，避免进入音频转写。</p>"
                "<p>第三段 shownotes，进一步说明内容结构和关键信息。</p>"
            ),
            episode_description="单集摘要",
            podcast_description="节目简介，不应作为正文。",
        )
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts_xiaoyuzhou_shownotes_audio"
        capture_dir.mkdir(parents=True, exist_ok=True)
        media_path = capture_dir / "source_audio.mp3"
        media_path.write_bytes(b"audio")

        with patch("app.extractors._fetch_text", return_value=("https://www.xiaoyuzhoufm.com/episode/demo", html)), patch(
            "app.extractors._download_file",
            return_value=media_path,
        ), patch("app.extractors._probe_media_duration", return_value=123.0):
            outcome = extract_xiaoyuzhou(
                SimpleNamespace(normalized_url="https://www.xiaoyuzhoufm.com/episode/demo"),
                capture_dir,
            )

        self.assertTrue(outcome.needs_transcription)
        self.assertEqual(outcome.primary_text, "")
        self.assertEqual(outcome.notes_text, "")
        self.assertEqual(outcome.strategy, ["episode_audio"])
        media_path.unlink(missing_ok=True)

    def test_xiaoyuzhou_episode_description_still_requires_transcription(self) -> None:
        html = self._xiaoyuzhou_episode_html(
            episode_description=(
                "这是一段单集正文摘要，应该作为主结果交付。"
                "它描述的是这一期的具体内容，而不是整个播客节目的简介。"
                "当 shownotes 缺失时，应当直接命中这里。"
                "这里继续补充本期讨论的背景、案例和结论，让正文长度足以构成可交付文本。"
                "这样可以确保它命中单集 description 快路径，而不是退回音频转写。"
                "这里再补一段关于节目观点、论据和结尾判断的延展内容，用来模拟真实的小宇宙单集正文。"
                "继续补充主持人提到的行业变化、案例细节和实际影响，让这段 description 明显超过最小正文阈值。"
                "最后再增加一段总结，说明为什么这类单集正文应该直接交付，而不是退回到音频下载和转写流程。"
            ),
            podcast_description="节目简介，不应作为正文。",
        )
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts_xiaoyuzhou_description_audio"
        capture_dir.mkdir(parents=True, exist_ok=True)
        media_path = capture_dir / "source_audio.mp3"
        media_path.write_bytes(b"audio")

        with patch("app.extractors._fetch_text", return_value=("https://www.xiaoyuzhoufm.com/episode/demo", html)), patch(
            "app.extractors._download_file",
            return_value=media_path,
        ), patch("app.extractors._probe_media_duration", return_value=123.0):
            outcome = extract_xiaoyuzhou(
                SimpleNamespace(normalized_url="https://www.xiaoyuzhoufm.com/episode/demo"),
                capture_dir,
            )

        self.assertTrue(outcome.needs_transcription)
        self.assertEqual(outcome.primary_text, "")
        self.assertEqual(outcome.notes_text, "")
        self.assertEqual(outcome.strategy, ["episode_audio"])
        media_path.unlink(missing_ok=True)

    def test_xiaoyuzhou_podcast_description_only_requires_transcription(self) -> None:
        html = self._xiaoyuzhou_episode_html(
            podcast_description="这是节目简介，不是这期正文，不应该直接交付。",
        )
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts_xiaoyuzhou_audio"
        capture_dir.mkdir(parents=True, exist_ok=True)
        media_path = capture_dir / "source_audio.mp3"
        media_path.write_bytes(b"audio")

        with patch("app.extractors._fetch_text", return_value=("https://www.xiaoyuzhoufm.com/episode/demo", html)), patch(
            "app.extractors._download_file",
            return_value=media_path,
        ), patch("app.extractors._probe_media_duration", return_value=123.0):
            outcome = extract_xiaoyuzhou(
                SimpleNamespace(normalized_url="https://www.xiaoyuzhoufm.com/episode/demo"),
                capture_dir,
            )

        self.assertTrue(outcome.needs_transcription)
        self.assertEqual(outcome.notes_text, "")
        self.assertEqual(outcome.primary_text, "")
        self.assertEqual(outcome.strategy, ["episode_audio"])
        media_path.unlink(missing_ok=True)

    def test_xiaoyuzhou_shownotes_do_not_mix_comments(self) -> None:
        html = self._xiaoyuzhou_episode_html(
            shownotes=(
                "<p>第一段正式正文。</p>"
                "<p>第二段正式正文。</p>"
                "<p>第三段正式正文。</p>"
            ),
            episode_description="短摘要",
            podcast_description="节目简介",
        )
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts_xiaoyuzhou_comment_audio"
        capture_dir.mkdir(parents=True, exist_ok=True)
        media_path = capture_dir / "source_audio.mp3"
        media_path.write_bytes(b"audio")

        with patch("app.extractors._fetch_text", return_value=("https://www.xiaoyuzhoufm.com/episode/demo", html)), patch(
            "app.extractors._download_file",
            return_value=media_path,
        ), patch("app.extractors._probe_media_duration", return_value=123.0):
            outcome = extract_xiaoyuzhou(
                SimpleNamespace(normalized_url="https://www.xiaoyuzhoufm.com/episode/demo"),
                capture_dir,
            )

        self.assertEqual(outcome.notes_text, "")
        self.assertEqual(outcome.primary_text, "")
        self.assertTrue(outcome.needs_transcription)
        media_path.unlink(missing_ok=True)

    def test_merge_result_sets_transcript_status(self) -> None:
        capture = SimpleNamespace(title="sample")
        source = SourceMetaModel(platform="youtube", content_type="video")

        subtitle_result = pipeline._merge_result(
            capture,
            source,
            ExtractionOutcome(
                platform="youtube",
                content_type="video",
                title="sample",
                subtitle_text="hello subtitle",
            ),
            None,
        )
        self.assertEqual(subtitle_result.transcript_status, "subtitle")

        transcribed_result = pipeline._merge_result(
            capture,
            source,
            ExtractionOutcome(
                platform="youtube",
                content_type="video",
                title="sample",
                needs_transcription=True,
            ),
            SimpleNamespace(transcript_text="hello transcript", timeline_text="", provider="mock", segment_count=1),
        )
        self.assertEqual(transcribed_result.transcript_status, "transcribed")

        silent_video_result = pipeline._merge_result(
            capture,
            SourceMetaModel(platform="xiaohongshu", content_type="video"),
            ExtractionOutcome(
                platform="xiaohongshu",
                content_type="video",
                title="sample",
                article_text="这是一段无语音视频的页面文案，用来在没有字幕和转写时兜底交付。",
                description="这是一段无语音视频的页面文案，用来在没有字幕和转写时兜底交付。",
                needs_transcription=True,
            ),
            SimpleNamespace(transcript_text="", timeline_text="", provider="mock", segment_count=0),
        )
        self.assertEqual(silent_video_result.transcript_status, "skipped")
        self.assertEqual(silent_video_result.text_source, "article")

    def test_merge_result_rejects_xiaoyuzhou_page_notes(self) -> None:
        capture = SimpleNamespace(title="sample")
        source = SourceMetaModel(platform="xiaoyuzhou", content_type="audio")

        result = pipeline._merge_result(
            capture,
            source,
            ExtractionOutcome(
                platform="xiaoyuzhou",
                content_type="audio",
                title="sample",
                notes_text="第一段 notes。\n第二段 notes。\n第三段 notes。",
                primary_text="第一段 notes。\n第二段 notes。\n第三段 notes。",
            ),
            None,
        )

        self.assertEqual(result.text_source, "none")
        self.assertEqual(result.primary_result_type, "")
        self.assertEqual(result.primary_text, "")
        self.assertEqual(result.notes_text, "")

    def test_video_primary_text_rejects_notes_only(self) -> None:
        primary_text, result_type, _, _, text_source = pipeline._select_primary_result(
            ExtractionOutcome(
                platform="douyin",
                content_type="video",
                title="sample",
                notes_text="这是页面简介，不应该被当成视频正文。",
                description="这是页面简介，不应该被当成视频正文。",
            ),
            "",
        )

        self.assertEqual(primary_text, "")
        self.assertEqual(result_type, "")
        self.assertEqual(text_source, "none")

    def test_silent_video_allows_page_copy_fallback(self) -> None:
        result = pipeline._merge_result(
            SimpleNamespace(title="sample"),
            SourceMetaModel(platform="douyin", content_type="video"),
            ExtractionOutcome(
                platform="douyin",
                content_type="video",
                title="sample",
                notes_text="sample\n\n#城市漫步# @demo 这是静音视频的页面文案，会在没有字幕和转写时作为交付结果。",
                description="sample\n\n#城市漫步# @demo 这是静音视频的页面文案，会在没有字幕和转写时作为交付结果。",
                needs_transcription=True,
            ),
            SimpleNamespace(transcript_text="", timeline_text="", provider="mock", segment_count=0),
        )

        self.assertEqual(result.primary_text, "这是静音视频的页面文案，会在没有字幕和转写时作为交付结果。")
        self.assertEqual(result.primary_result_type, "notes")
        self.assertEqual(result.text_source, "notes")

    def test_douyin_browser_video_keeps_page_copy_for_silent_fallback_only(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://www.douyin.com/video/1234567890")
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts_douyin"
        capture_dir.mkdir(parents=True, exist_ok=True)
        browser_result = SimpleNamespace(
            title="douyin sample",
            final_url="https://www.douyin.com/video/1234567890",
            body_text="这是一段很长的页面说明文案，不应该作为视频正文直接交付。" * 4,
            media_url="https://example.com/douyin.mp4",
            image_urls=[],
        )

        with patch.object(source_adapters, "fetch_douyin_media", return_value=browser_result), patch.object(
            source_adapters, "_download_browser_media", return_value=capture_dir / "douyin_browser_source.mp4"
        ):
            outcome = source_adapters.DouyinAdapter()._browser_extract(resolved, capture_dir)

        self.assertEqual(outcome.content_type, "video")
        self.assertEqual(outcome.primary_text, "")
        self.assertTrue(bool(outcome.notes_text))
        self.assertTrue(bool(outcome.description))
        self.assertTrue(outcome.needs_transcription)

    def test_douyin_browser_video_keeps_chapter_outline_out_of_primary_text(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://www.douyin.com/video/1234567890")
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts_douyin_chapters"
        capture_dir.mkdir(parents=True, exist_ok=True)
        browser_result = SimpleNamespace(
            title="douyin chapter sample",
            final_url="https://www.douyin.com/video/1234567890",
            body_text="00:00 第一首歌\n03:21 第二首歌\n06:48 第三首歌",
            media_url="https://example.com/douyin.mp4",
            image_urls=[],
        )

        with patch.object(source_adapters, "fetch_douyin_media", return_value=browser_result), patch.object(
            source_adapters, "_download_browser_media", return_value=capture_dir / "douyin_browser_source.mp4"
        ):
            outcome = source_adapters.DouyinAdapter()._browser_extract(resolved, capture_dir)

        self.assertEqual(outcome.subtitle_text, "")
        self.assertEqual(outcome.primary_text, "")
        self.assertEqual(outcome.subtitle_source, "none")
        self.assertTrue(outcome.needs_transcription)
        self.assertIn("chapter_outline", outcome.strategy)
        self.assertTrue(any("章节目录" in warning for warning in outcome.warnings))

    def test_xiaohongshu_browser_video_keeps_page_copy_for_silent_fallback_only(self) -> None:
        resolved = SimpleNamespace(normalized_url="https://www.xiaohongshu.com/explore/demo")
        capture_dir = Path(tempfile.gettempdir()) / "audio2text_platform_contracts_xhs"
        capture_dir.mkdir(parents=True, exist_ok=True)
        browser_result = SimpleNamespace(
            title="xhs sample",
            final_url="https://www.xiaohongshu.com/explore/demo",
            body_text="这是一段小红书视频页面文案，不应该作为视频主结果。" * 4,
            media_url="https://example.com/xhs.mp4",
            image_urls=[],
        )

        with patch.object(source_adapters, "fetch_xiaohongshu_page", return_value=browser_result), patch.object(
            source_adapters, "_download_browser_media", return_value=capture_dir / "xiaohongshu_browser_source.mp4"
        ):
            outcome = source_adapters.XiaohongshuAdapter()._browser_extract(resolved, capture_dir)

        self.assertEqual(outcome.content_type, "video")
        self.assertEqual(outcome.primary_text, "")
        self.assertTrue(bool(outcome.article_text))
        self.assertTrue(bool(outcome.description))
        self.assertTrue(outcome.needs_transcription)

    def test_capture_envelope_exposes_only_safe_delivery_fields(self) -> None:
        source = SourceMetaModel(
            platform="youtube",
            content_type="video",
            canonical_url="https://youtu.be/demo",
            source_item_id="secret-id",
            extractor_used="open_source_provider",
            fallback_used=True,
            fallback_chain=["direct_provider", "open_source_provider"],
        )
        result = ResultDocumentModel(
            primary_text="hello",
            primary_result_type="subtitle",
            transcript_status="subtitle",
            text_source="subtitle",
            subtitle_source="manual",
            selected_language="en",
        )
        processing = ProcessingStateModel(
            retry_count=1,
            retryable=True,
            failure_reason_code="download_failed",
            progress_percent=85,
            progress_detail="已拿到可用字幕，正在跳过转写并整理结果。",
        )
        capture = SimpleNamespace(
            id="cap123",
            status="done",
            input_type="url",
            title="sample",
            created_at="2026-04-05 12:00:00",
            updated_at="2026-04-05 12:00:01",
            started_at="2026-04-05 12:00:00",
            finished_at="2026-04-05 12:00:01",
            error_stage=None,
            error_message=None,
            source=source,
            result=result,
            processing=processing,
        )

        envelope = _to_capture_envelope(capture)

        self.assertTrue(envelope.capture.retryable)
        self.assertEqual(envelope.capture.progress_percent, 85)
        self.assertEqual(envelope.source.extractor_used, "open_source_provider")
        self.assertTrue(envelope.source.fallback_used)
        self.assertEqual(envelope.quality.transcript_status, "subtitle")
        self.assertEqual(envelope.quality.text_source, "subtitle")
        self.assertEqual(envelope.quality.subtitle_source, "manual")
        self.assertEqual(envelope.quality.selected_language, "en")
        self.assertEqual(envelope.source.canonical_url, "https://youtu.be/demo")
        self.assertFalse(hasattr(envelope.source, "source_item_id"))

    def test_merge_result_prefers_transcription_language_over_source_language(self) -> None:
        capture = SimpleNamespace(title="sample")
        source = SourceMetaModel(platform="douyin", content_type="video", language="zh")

        result = pipeline._merge_result(
            capture,
            source,
            ExtractionOutcome(
                platform="douyin",
                content_type="video",
                title="sample",
                needs_transcription=True,
            ),
            SimpleNamespace(
                transcript_text="Enjoy the moment.",
                timeline_text="",
                provider="mock",
                language="en",
                segment_count=1,
            ),
        )

        self.assertEqual(result.text_source, "transcript")
        self.assertEqual(result.selected_language, "en")

    def test_douyin_chapter_text_rejects_absurd_timestamps(self) -> None:
        detail = {
            "duration": 116000,
            "chapter_list": [
                {"timestamp": 10000, "desc": "part 1"},
                {"timestamp": 166000, "desc": "part 2"},
                {"timestamp": 920000, "desc": "part 3"},
            ],
        }

        self.assertEqual(_douyin_chapter_text(detail), "")

    def test_local_transcribe_language_defaults_to_auto_detection(self) -> None:
        self.assertIsNone(_normalize_requested_language("auto"))
        self.assertIsNone(_normalize_requested_language(""))
        self.assertEqual(_normalize_requested_language("en"), "en")

    def test_local_transcribe_only_normalizes_chinese_output(self) -> None:
        self.assertTrue(_should_convert_to_simplified("zh"))
        self.assertTrue(_should_convert_to_simplified("yue"))
        self.assertFalse(_should_convert_to_simplified("en"))

    def test_external_error_classification_is_platform_aware(self) -> None:
        self.assertEqual(
            _classify_external_error("ERROR: [youtube] aaaaaaaaaaa: Video unavailable", platform="youtube", stage="extract"),
            ("youtube_video_unavailable", False),
        )
        self.assertEqual(
            _classify_external_error("ERROR: [Douyin] 123456: Fresh cookies are needed", platform="douyin", stage="extract"),
            ("douyin_fresh_cookies_required", True),
        )
        self.assertEqual(
            _classify_external_error("ERROR: 1111111111: KeyError('bvid')", platform="bilibili", stage="extract"),
            ("bilibili_video_invalid", False),
        )
        self.assertEqual(
            _classify_external_error("HTTP Error 412: Precondition Failed", platform="bilibili", stage="extract"),
            ("bilibili_extract_precondition_failed", True),
        )

    def test_public_error_message_prefers_reason_code(self) -> None:
        self.assertEqual(
            pipeline._public_error_message_with_reason("extract", "youtube_video_unavailable"),
            "这条 YouTube 视频当前不可访问、已下架，或不再公开。请换一条公开视频再试。",
        )
        self.assertEqual(
            pipeline._public_error_message_with_reason("extract", "douyin_fresh_cookies_required"),
            "这条抖音内容当前需要更新浏览器会话后再试。你可以稍后重试，或换一条公开视频。",
        )
        self.assertEqual(
            pipeline._public_error_message_with_reason("extract", "xiaohongshu_full_url_required"),
            "这条小红书内容请从浏览器地址栏复制完整网页链接后再试。",
        )

    def test_public_error_message_covers_browser_session_reasons(self) -> None:
        self.assertEqual(
            pipeline._public_error_message_with_reason("extract", "browser_runtime_missing"),
            "项目级浏览器运行时还没有准备好，当前这条内容无法走浏览器会话提取。请先补齐运行时后再试。",
        )
        self.assertEqual(
            pipeline._public_error_message_with_reason("extract", "browser_session_expired"),
            "当前内容需要有效的项目级浏览器会话后才能继续处理。请刷新会话后再试。",
        )
        self.assertEqual(
            pipeline._public_error_message_with_reason("extract", "browser_challenge_required"),
            "当前页面触发了平台验证，暂时需要刷新项目级浏览器会话后再试。",
        )

    def test_fast_path_detail_marks_quick_path_explicitly(self) -> None:
        subtitle_detail = pipeline._fast_path_detail(
            ExtractionOutcome(
                platform="youtube",
                content_type="video",
                title="sample",
                subtitle_text="hello",
            )
        )
        self.assertIn("字幕", subtitle_detail)

        browser_detail = pipeline._fast_path_detail(
            ExtractionOutcome(
                platform="xiaohongshu",
                content_type="video",
                title="sample",
                strategy=["browser_session", "media_direct"],
            )
        )
        self.assertIn("浏览器会话", browser_detail)


    def test_public_error_message_handles_youtube_network_timeout(self) -> None:
        self.assertEqual(
            pipeline._public_error_message_with_reason("extract", "youtube_extract_timeout"),
            "当前网络环境没有稳定连上 YouTube。请稍后重试，或更换可访问 YouTube 的网络后再试。",
        )


if __name__ == "__main__":
    unittest.main()

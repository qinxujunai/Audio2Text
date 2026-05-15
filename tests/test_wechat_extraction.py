from __future__ import annotations

import unittest

from app.extractors import ExtractionOutcome, _extract_wechat_article_text, _extract_wechat_image_urls, _normalize_wechat_text
from app.pipeline import _select_primary_result
from app.browser_provider import _wechat_image_dedupe_key
from app.source_adapters import _looks_like_wechat_blocked


WECHAT_SAMPLE_HTML = """
<html>
  <body>
    <div id="js_content">
      <section>
        <p><span>OpenClaw</span><strong>Token</strong></p>
        <p>MacMini is mentioned here.</p>
        <div class="media-wrap">
          <img data-src="https://mmbiz.qpic.cn/test/body-image-1?wx_fmt=jpeg" data-w="640" data-h="960" />
        </div>
        <section>
          <p><span>Another paragraph</span> deploy follow-up.</p>
        </section>
      </section>
    </div>
    <div class="outside">
      <p>This paragraph should stay outside.</p>
      <img data-src="https://mmbiz.qpic.cn/test/outside-image?wx_fmt=jpeg" data-w="640" data-h="960" />
    </div>
  </body>
</html>
"""

WECHAT_SRC_IMAGE_HTML = """
<html>
  <body>
    <div id="js_content">
      <p>Gallery paragraph</p>
      <img src="https://mmbiz.qpic.cn/test/body-image-src?wx_fmt=jpeg" />
    </div>
  </body>
</html>
"""


class WechatExtractionTestCase(unittest.TestCase):
    def test_extracts_nested_text_inside_js_content(self) -> None:
        text = _extract_wechat_article_text(WECHAT_SAMPLE_HTML)

        self.assertIn("OpenClaw", text)
        self.assertIn("Token", text)
        self.assertIn("MacMini", text)
        self.assertIn("deploy", text)
        self.assertNotIn("This paragraph should stay outside.", text)

    def test_extracts_images_only_from_js_content_scope(self) -> None:
        urls = _extract_wechat_image_urls(WECHAT_SAMPLE_HTML, "https://mp.weixin.qq.com/s/test")

        self.assertEqual(len(urls), 1)
        self.assertIn("body-image-1", urls[0])
        self.assertNotIn("outside-image", urls[0])

    def test_extracts_images_from_plain_src_attribute(self) -> None:
        urls = _extract_wechat_image_urls(WECHAT_SRC_IMAGE_HTML, "https://mp.weixin.qq.com/s/test")

        self.assertEqual(len(urls), 1)
        self.assertIn("body-image-src", urls[0])

    def test_normalizes_literal_wechat_newline_escape_sequences(self) -> None:
        text = _normalize_wechat_text("第一段\\x0a\\x0a第二段\\n第三段")

        self.assertEqual(text, "第一段\n\n第二段\n第三段")

    def test_detects_wechat_captcha_redirect_as_blocked(self) -> None:
        outcome = ExtractionOutcome(
            platform="wechat_article",
            content_type="article",
            title="微信公众平台",
            canonical_url="https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?target_url=https%3A%2F%2Fmp.weixin.qq.com%2Fs%2Fdemo",
            article_text="完成以下验证后继续访问",
            primary_text="完成以下验证后继续访问",
        )

        self.assertTrue(_looks_like_wechat_blocked(outcome))

    def test_dedupes_wechat_image_variants_with_same_asset_path(self) -> None:
        first = "https://mmbiz.qpic.cn/sz_mmbiz_png/example/0?from=appmsg&wxfrom=12&wx_fmt=png&tp=webp&usePicPrefetch=1"
        second = "https://mmbiz.qpic.cn/sz_mmbiz_png/example/0?wx_fmt=png&from=appmsg&wxfrom=12"

        self.assertEqual(_wechat_image_dedupe_key(first), _wechat_image_dedupe_key(second))

    def test_short_image_article_text_is_not_marked_substantial(self) -> None:
        outcome = ExtractionOutcome(
            platform="wechat_article",
            content_type="image_article",
            title="sample",
            article_text="short single-line article text",
            image_urls=["https://mmbiz.qpic.cn/test/body-image-1?wx_fmt=jpeg"],
        )

        primary, result_type, confidence, completeness, text_source = _select_primary_result(outcome, "")

        self.assertEqual(primary, "short single-line article text")
        self.assertEqual(result_type, "article")
        self.assertEqual(confidence, "low")
        self.assertEqual(completeness, "partial")
        self.assertEqual(text_source, "article")


if __name__ == "__main__":
    unittest.main()

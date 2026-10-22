from __future__ import annotations

import unittest

from scripts import benchmark_extraction


class BenchmarkExtractionTestCase(unittest.TestCase):
    def test_content_identity_deduplicates_aliases_and_keeps_article_parts(self):
        self.assertEqual(benchmark_extraction._content_identity('youtube', 'https://youtu.be/abc'), benchmark_extraction._content_identity('youtube', 'https://www.youtube.com/watch?v=abc&utm_source=test'))
        self.assertNotEqual(benchmark_extraction._content_identity('wechat_article', 'https://mp.weixin.qq.com/s?__biz=a&mid=1&idx=1'), benchmark_extraction._content_identity('wechat_article', 'https://mp.weixin.qq.com/s?__biz=a&mid=1&idx=2'))
        self.assertIsNone(benchmark_extraction._content_identity('douyin', 'https://v.douyin.com/short/'))
    def test_replacement_requires_five_point_success_or_twenty_percent_p95_gain(self) -> None:
        baseline = {
            "success_rate": 0.90,
            "p95_seconds": 10.0,
            "release_dataset_ready": True,
        }
        faster = {
            "success_rate": 0.90,
            "p95_seconds": 7.5,
            "release_dataset_ready": True,
        }
        decision = benchmark_extraction._replacement_decision(baseline, faster)
        self.assertTrue(decision["eligible_to_replace_default"])

        unready = dict(faster, release_dataset_ready=False)
        decision = benchmark_extraction._replacement_decision(baseline, unready)
        self.assertFalse(decision["eligible_to_replace_default"])

    def test_summary_requires_sixty_links_and_three_runs(self) -> None:
        results = [
            {
                "id": f"sample-{sample}",
                "repeat": repeat,
                "success": True,
                "complete": True,
                "duration_seconds": 1.0,
                "image_count": 1,
                "media_available": True,
                "needs_transcription": False,
                "text_chars": 100,
                "fallback_used": False,
            }
            for repeat in range(1, 4)
            for sample in range(60)
        ]
        self.assertTrue(benchmark_extraction._summarize(results)["release_dataset_ready"])

        incomplete = [item for item in results if item["id"] != "sample-0" or item["repeat"] == 1]
        self.assertFalse(benchmark_extraction._summarize(incomplete)["release_dataset_ready"])

    def test_summary_reports_timeboxed_provider_runs(self) -> None:
        result = {
            "id": "sample-1",
            "repeat": 1,
            "success": False,
            "complete": False,
            "duration_seconds": 180.0,
            "image_count": 0,
            "media_available": False,
            "needs_transcription": False,
            "text_chars": 0,
            "fallback_used": False,
            "error_code": "benchmark_timeout",
        }
        summary = benchmark_extraction._summarize([result])
        self.assertEqual(summary["timeout_rate"], 1.0)
        self.assertFalse(summary["release_dataset_ready"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import benchmark_transcription


class BenchmarkTranscriptionTestCase(unittest.TestCase):
    def test_char_error_rate_ignores_spacing(self) -> None:
        self.assertEqual(
            benchmark_transcription._char_error_rate("开放时间 9 点", "开放时间9点"),
            0,
        )

    def test_manifest_loads_relative_audio_and_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            audio = root / "sample.wav"
            audio.write_bytes(b"")
            manifest = root / "samples.jsonl"
            manifest.write_text(
                '{"id":"sample","audio":"sample.wav","reference":"hello","terms":["CUDA","Whisper"],"slice":"noise","weight":2}\n',
                encoding="utf-8",
            )

            samples = benchmark_transcription._load_manifest(manifest)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].sample_id, "sample")
        self.assertEqual(samples[0].audio_path, audio)
        self.assertEqual(samples[0].reference, "hello")
        self.assertEqual(samples[0].terms, ("CUDA", "Whisper"))
        self.assertEqual(samples[0].slice_name, "noise")
        self.assertEqual(samples[0].weight, 2)

    def test_summary_reports_release_metrics(self) -> None:
        summary = benchmark_transcription._summarize(
            [
                {
                    "rtf": 0.1,
                    "char_error_rate": 0.2,
                    "weight": 2,
                    "duration_seconds": 60,
                    "first_segment_seconds": 0.4,
                    "term_count": 2,
                    "term_hits": ["one"],
                    "slice": "noise",
                    "silence_hallucination": False,
                },
                {
                    "rtf": 0.3,
                    "char_error_rate": 0.1,
                    "weight": 1,
                    "duration_seconds": 120,
                    "first_segment_seconds": 0.8,
                    "term_count": 1,
                    "term_hits": ["two"],
                    "slice": "mandarin",
                    "silence_hallucination": True,
                },
            ]
        )

        self.assertEqual(summary["weighted_char_error_rate"], 0.1667)
        self.assertEqual(summary["term_recall"], 0.6667)
        self.assertEqual(summary["silence_hallucination_count"], 1)
        self.assertFalse(benchmark_transcription._release_dataset_ready(summary))


if __name__ == "__main__":
    unittest.main()

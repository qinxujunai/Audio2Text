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
                '{"id":"sample","audio":"sample.wav","reference":"hello","terms":["CUDA","Whisper"]}\n',
                encoding="utf-8",
            )

            samples = benchmark_transcription._load_manifest(manifest)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].sample_id, "sample")
        self.assertEqual(samples[0].audio_path, audio)
        self.assertEqual(samples[0].reference, "hello")
        self.assertEqual(samples[0].terms, ("CUDA", "Whisper"))


if __name__ == "__main__":
    unittest.main()

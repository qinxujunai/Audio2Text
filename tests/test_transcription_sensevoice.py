from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.providers import transcription


class _FakeStream:
    def __init__(self) -> None:
        self.result = SimpleNamespace(text="轻量模型转写成功")
        self.accepted = None

    def accept_waveform(self, sample_rate, samples) -> None:
        self.accepted = (sample_rate, samples)


class _FakeRecognizer:
    def __init__(self) -> None:
        self.stream = _FakeStream()

    def create_stream(self):
        return self.stream

    def decode_stream(self, stream) -> None:
        self.decoded = stream


class SenseVoiceProviderTestCase(unittest.TestCase):
    def test_provider_returns_normalized_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            media = Path(raw_root) / "sample.wav"
            media.write_bytes(b"fixture")
            provider = transcription.LocalSenseVoiceProvider(raw_root)
            recognizer = _FakeRecognizer()
            progress = []
            with (
                patch.object(provider, "_decode_audio", return_value=[0.0, 0.1]),
                patch.object(provider, "_recognizer", return_value=recognizer),
            ):
                result = provider.transcribe(
                    str(media),
                    capture_id="capture",
                    output_folder=raw_root,
                    progress_callback=lambda ratio, detail: progress.append((ratio, detail)),
                )

        self.assertEqual(result.provider, "local_sensevoice")
        self.assertEqual(result.transcript_text, "轻量模型转写成功")
        self.assertEqual(recognizer.stream.accepted[0], 16000)
        self.assertEqual([item[0] for item in progress], [0.1, 1.0])

    def test_auto_prefers_sensevoice_on_cpu_without_whisper_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            model_dir = Path(raw_root)
            (model_dir / "model.int8.onnx").write_bytes(b"model")
            (model_dir / "tokens.txt").write_text("tokens", encoding="utf-8")
            with (
                patch.object(transcription, "TRANSCRIPTION_PROVIDER", "auto"),
                patch.object(transcription, "DEVICE", "cpu"),
                patch.object(transcription, "MODEL_PATH", model_dir / "missing"),
                patch.object(transcription, "SENSEVOICE_MODEL_DIR", model_dir),
            ):
                provider = transcription.get_transcription_provider()

        self.assertIsInstance(provider, transcription.LocalSenseVoiceProvider)
        self.assertEqual(provider.model_dir, model_dir)


if __name__ == "__main__":
    unittest.main()

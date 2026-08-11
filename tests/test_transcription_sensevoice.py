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
        self.streams = []

    def create_stream(self):
        stream = _FakeStream()
        self.stream = stream
        self.streams.append(stream)
        return stream

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

    def test_provider_chunks_long_audio_before_recognition(self) -> None:
        sample_count = transcription.LocalSenseVoiceProvider.sample_rate * 65
        samples = [0.1] * sample_count
        with tempfile.TemporaryDirectory() as raw_root:
            media = Path(raw_root) / "long.wav"
            media.write_bytes(b"fixture")
            provider = transcription.LocalSenseVoiceProvider(raw_root)
            recognizer = _FakeRecognizer()
            with (
                patch.object(provider, "_decode_audio", return_value=samples),
                patch.object(provider, "_recognizer", return_value=recognizer),
            ):
                result = provider.transcribe(
                    str(media),
                    capture_id="capture",
                    output_folder=raw_root,
                )

        self.assertGreater(len(recognizer.streams), 1)
        self.assertTrue(all(len(stream.accepted[1]) <= 28 * 16000 for stream in recognizer.streams))
        self.assertEqual(result.segment_count, len(recognizer.streams))
        self.assertEqual(result.transcript_text.count("轻量模型转写成功"), len(recognizer.streams))

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

    def test_auto_prefers_sensevoice_for_chinese_platform_even_with_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            model_dir = Path(raw_root)
            (model_dir / "model.int8.onnx").write_bytes(b"model")
            (model_dir / "tokens.txt").write_text("tokens", encoding="utf-8")
            whisper_dir = model_dir / "whisper"
            whisper_dir.mkdir()
            with (
                patch.object(transcription, "TRANSCRIPTION_PROVIDER", "auto"),
                patch.object(transcription, "DEVICE", "auto"),
                patch.object(transcription, "MODEL_PATH", whisper_dir),
                patch.object(transcription, "SENSEVOICE_MODEL_DIR", model_dir),
                patch.object(transcription, "_nvidia_gpu_memory_mb", return_value=6144),
            ):
                provider = transcription.get_transcription_provider(platform="bilibili")

        self.assertIsInstance(provider, transcription.LocalSenseVoiceProvider)

    def test_auto_uses_gpu_whisper_with_cpu_fallback_for_unknown_language(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            model_dir = Path(raw_root)
            (model_dir / "model.int8.onnx").write_bytes(b"model")
            (model_dir / "tokens.txt").write_text("tokens", encoding="utf-8")
            whisper_dir = model_dir / "whisper"
            whisper_dir.mkdir()
            with (
                patch.object(transcription, "TRANSCRIPTION_PROVIDER", "auto"),
                patch.object(transcription, "DEVICE", "auto"),
                patch.object(transcription, "MODEL_PATH", whisper_dir),
                patch.object(transcription, "SENSEVOICE_MODEL_DIR", model_dir),
                patch.object(transcription, "_nvidia_gpu_memory_mb", return_value=6144),
            ):
                provider = transcription.get_transcription_provider(platform="youtube")

        self.assertIsInstance(provider, transcription.FallbackTranscriptionProvider)
        self.assertIsInstance(provider.primary, transcription.LocalFasterWhisperProvider)
        self.assertIsInstance(provider.fallback, transcription.LocalSenseVoiceProvider)


if __name__ == "__main__":
    unittest.main()

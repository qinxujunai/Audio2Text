from __future__ import annotations

import tempfile
import unittest
import weakref
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts import benchmark_transcription


class BenchmarkTranscriptionTestCase(unittest.TestCase):
    def test_checkpoint_resumes_complete_results_and_rejects_changed_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"checkpoint.jsonl"
            identity = {"model_sha256": "first", "terms": ("hello",)}
            checkpoint = benchmark_transcription._BenchmarkCheckpoint(path, identity)
            key = ("model", "weights", "cuda", 1, "sample")
            checkpoint.record(key, {"transcript": "hello", "repeat": 1})
            resumed = benchmark_transcription._BenchmarkCheckpoint(path, identity)
            self.assertEqual(resumed.results[key]["transcript"], "hello")
            with self.assertRaises(ValueError):
                benchmark_transcription._BenchmarkCheckpoint(path, {"model_sha256": "changed"})

    def test_whisper_is_released_before_loading_cuda_candidate(self) -> None:
        live = []
        class FakeWhisper:
            def __init__(self, *args, **kwargs):
                live.append(weakref.ref(self))
        provider = MagicMock()
        provider.worker.process.pid = 1234
        def cuda_constructor(*args):
            self.assertIsNone(live[0](), "Previous Whisper allocations may still be resident")
            return provider
        args = Namespace(language="auto", manifest="fixture.jsonl", limit=None, files=[], model=["whisper=weights"],
                         device="cuda", compute_type="float16", beam_size=1, vad_filter=False,
                         sensevoice_model=[], sensevoice_cuda_model=["gpu=weights"],
                         sensevoice_cuda_sdk="sdk", sensevoice_threads=4, repeat=1)
        sample = benchmark_transcription.BenchmarkSample("sample", Path("sample.wav"), "hello")
        def result(*args, **kwargs):
            return {"id": "sample", "duration_seconds": 1.0, "rtf": 0.1, "char_error_rate": 0.0}
        with patch.object(benchmark_transcription, "_load_manifest", return_value=[sample]), \
             patch.object(benchmark_transcription, "WhisperModel", new=FakeWhisper), \
             patch.object(benchmark_transcription, "LocalSenseVoiceCudaProvider", new=cuda_constructor), \
             patch.object(benchmark_transcription, "_transcribe_sample", new=result), \
             patch.object(benchmark_transcription, "_transcribe_sensevoice_sample", new=result), \
             patch.object(benchmark_transcription, "_ResourceSampler") as sampler, \
             patch.object(benchmark_transcription, "_hardware_profile", return_value={}), \
             patch.object(benchmark_transcription, "_path_size_bytes", return_value=100):
            sampler.return_value.peak_gpu_memory_mb = 0
            benchmark_transcription.run_benchmark(args)

    def test_gpu_memory_does_not_treat_unknown_worker_usage_as_zero(self) -> None:
        output = SimpleNamespace(returncode=0, stdout="10,600\n20,N/A\n")
        with patch.object(benchmark_transcription.subprocess, "run", return_value=output):
            self.assertIsNone(benchmark_transcription._gpu_process_memory_mb({10, 20}))

    def test_whisper_segment_boundaries_preserve_english_word_spacing(self) -> None:
        model = MagicMock()
        model.transcribe.return_value = ([SimpleNamespace(text="Hello"), SimpleNamespace(text=" world")], SimpleNamespace())
        sample = benchmark_transcription.BenchmarkSample("sample", Path("sample.wav"), "Hello world")
        with patch.object(benchmark_transcription, "_duration_seconds", return_value=1.0):
            result = benchmark_transcription._transcribe_sample(model, sample, language=None, beam_size=1, vad_filter=False)
        self.assertEqual(result["transcript"], "Hello world")
        self.assertEqual(result["word_error_rate"], 0.0)

    def test_cuda_benchmark_uses_isolated_worker_and_reports_actual_weight_type(self) -> None:
        sample = benchmark_transcription.BenchmarkSample("sample", Path("sample.wav"), "hello")
        provider = MagicMock()
        provider.worker.process.pid = 1234
        args = Namespace(language="auto", manifest="fixture.jsonl", limit=None, files=[],
                         model=[], sensevoice_model=[], sensevoice_cuda_model=["gpu=weights"],
                         sensevoice_cuda_sdk="sdk", sensevoice_threads=4, repeat=1)
        result = {"id": "sample", "duration_seconds": 1.0, "rtf": 0.1,
                  "char_error_rate": 0.0, "mixed_error_rate": 0.0, "slice": "english"}
        with patch.object(benchmark_transcription, "_load_manifest", return_value=[sample]), \
             patch.object(benchmark_transcription, "LocalSenseVoiceCudaProvider", return_value=provider) as constructor, \
             patch.object(benchmark_transcription, "_transcribe_sensevoice_sample", return_value=result), \
             patch.object(benchmark_transcription, "_ResourceSampler") as sampler, \
             patch.object(benchmark_transcription, "_hardware_profile", return_value={}), \
             patch.object(benchmark_transcription, "_path_size_bytes", return_value=100):
            sampler.return_value.peak_gpu_memory_mb = 0
            report = benchmark_transcription.run_benchmark(args)
        constructor.assert_called_once_with(Path("weights"), Path("sdk"))
        provider.worker.warm.assert_called_once()
        provider.worker.close.assert_called_once()
        sampler.return_value.add_process.assert_called_once_with(1234)
        self.assertEqual(report["runs"][0]["device"], "cuda")
        self.assertEqual(report["runs"][0]["compute_type"], "float32")
        self.assertIsNone(report["runs"][0]["summary"]["peak_gpu_memory_mb"])

    def test_char_error_rate_ignores_spacing(self) -> None:
        self.assertEqual(
            benchmark_transcription._char_error_rate("开放时间 9 点", "开放时间9点"),
            0,
        )

    def test_word_and_mixed_error_rates_cover_english_and_code_switching(self) -> None:
        self.assertEqual(
            benchmark_transcription._word_error_rate("hello world", "hello word"),
            0.5,
        )
        self.assertEqual(
            benchmark_transcription._mixed_error_rate("今天用 CUDA 测试", "今天用 CUDA 测试"),
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

    def test_release_gate_requires_72_unique_samples_four_hours_and_three_runs(self) -> None:
        required_slices = {
            "mandarin": 0.1,
            "dialect": 0.1,
            "mixed_language": 0.1,
            "english": 0.1,
            "multi_speaker": 0.1,
            "noise": 0.1,
            "music": 0.1,
            "proper_nouns": 0.1,
            "long_form": 0.1,
        }
        summary = {
            "sample_count": 72,
            "audio_hours": 4.0,
            "run_count": 3,
            "failure_rate": 0.0,
            "slice_mixed_error_rate": required_slices,
        }
        self.assertTrue(benchmark_transcription._release_dataset_ready(summary))

    def test_candidate_gate_uses_absolute_quality_and_slice_thresholds(self) -> None:
        base = {
            "model": "baseline",
            "summary": {
                "weighted_mixed_error_rate": 0.10,
                "p95_rtf": 0.20,
                "slice_mixed_error_rate": {"mandarin": 0.10, "english": 0.10},
                "release_dataset_ready": True,
                "failure_rate": 0.0,
                "peak_gpu_memory_mb": 4000,
                "model_size_bytes": 1_000_000_000,
            },
        }
        faster = {
            "model": "candidate",
            "summary": {
                "weighted_mixed_error_rate": 0.104,
                "p95_rtf": 0.14,
                "slice_mixed_error_rate": {"mandarin": 0.109, "english": 0.104},
                "release_dataset_ready": True,
                "failure_rate": 0.0,
                "peak_gpu_memory_mb": 5000,
                "model_size_bytes": 1_500_000_000,
            },
        }
        decisions = benchmark_transcription._candidate_decisions([base, faster])
        self.assertTrue(decisions[0]["eligible_to_replace_default"])


if __name__ == "__main__":
    unittest.main()

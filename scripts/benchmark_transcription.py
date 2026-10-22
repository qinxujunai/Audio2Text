from __future__ import annotations

import argparse
import gc
import hashlib
import glob
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
from faster_whisper import WhisperModel

# Keep the documented file-path invocation working as well as
# ``python -m scripts.benchmark_transcription``.  When Python executes a
# script by path it puts ``scripts/`` on sys.path, not the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.providers.transcription import LocalSenseVoiceProvider, LocalSenseVoiceCudaProvider
from scripts.config import BEAM_SIZE, COMPUTE_TYPE, DEVICE, LANGUAGE, MODEL_PATH, VAD_FILTER


@dataclass(frozen=True)
class BenchmarkSample:
    sample_id: str
    audio_path: Path
    reference: str = ""
    terms: tuple[str, ...] = ()
    slice_name: str = "general"
    weight: float = 1.0
    expected_silence: bool = False
    reference_source: str = ""
    reference_kind: str = ""
    independent_audio: bool = True


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, ref_token in enumerate(reference, start=1):
        current = [row]
        for col, hyp_token in enumerate(hypothesis, start=1):
            cost = 0 if ref_token == hyp_token else 1
            current.append(
                min(
                    previous[col] + 1,
                    current[col - 1] + 1,
                    previous[col - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def _char_error_rate(reference: str, hypothesis: str) -> float | None:
    reference = _normalize_text(reference)
    hypothesis = _normalize_text(hypothesis)
    if not reference:
        return None
    return _edit_distance(list(reference), list(hypothesis)) / len(reference)


def _word_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:['’-][a-z0-9]+)*|[\u4e00-\u9fff]", str(value or "").lower())


def _word_error_rate(reference: str, hypothesis: str) -> float | None:
    reference_tokens = _word_tokens(reference)
    if not reference_tokens:
        return None
    return _edit_distance(reference_tokens, _word_tokens(hypothesis)) / len(reference_tokens)


def _mixed_error_rate(reference: str, hypothesis: str) -> float | None:
    # Han characters stay atomic while Latin words and numbers stay grouped.
    return _word_error_rate(reference, hypothesis)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _duration_seconds(path: Path) -> float:
    with av.open(str(path)) as container:
        if container.duration is not None:
            return float(container.duration / av.time_base)
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream and stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
    return 0.0


def _path_size_bytes(value: str) -> int | None:
    path = Path(value)
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return None


def _gpu_process_memory_mb(process_ids: set[int] | None = None) -> int | None:
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if output.returncode != 0:
        return None
    selected_pids = {str(pid) for pid in (process_ids or {os.getpid()})}
    total = 0
    for line in output.stdout.splitlines():
        values = [item.strip() for item in line.split(",")]
        if len(values) == 2 and values[0] in selected_pids:
            try:
                total += int(values[1])
            except ValueError:
                return None
    return total


class _ResourceSampler:
    def __init__(self) -> None:
        self.peak_gpu_memory_mb = 0
        self.gpu_memory_complete = True
        self.process_ids = {os.getpid()}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def add_process(self, pid: int) -> None:
        self.process_ids.add(pid)

    def _run(self) -> None:
        while not self._stop.wait(0.2):
            self._sample()

    def _sample(self) -> None:
        value = _gpu_process_memory_mb(self.process_ids.copy())
        if value is None:
            self.gpu_memory_complete = False
        else:
            self.peak_gpu_memory_mb = max(self.peak_gpu_memory_mb, value)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=4)
        self._sample()


def _failed_sample(sample: BenchmarkSample, exc: Exception, *, repeat: int) -> dict[str, Any]:
    return {
        "id": sample.sample_id,
        "audio": str(sample.audio_path),
        "duration_seconds": round(_duration_seconds(sample.audio_path), 3),
        "repeat": repeat,
        "slice": sample.slice_name,
        "weight": sample.weight,
        "expected_silence": sample.expected_silence,
        "error_code": "out_of_memory" if "out of memory" in str(exc).lower() else "transcription_failed",
        "error_type": type(exc).__name__,
        "rtf": None,
        "char_error_rate": None,
        "word_error_rate": None,
        "mixed_error_rate": None,
        "first_segment_seconds": None,
        "term_count": len(sample.terms),
        "term_hits": [],
    }


def _parse_model(value: str) -> tuple[str, str]:
    if "=" not in value:
        path = value.strip()
        return Path(path).name or path, path
    label, path = value.split("=", 1)
    return label.strip() or Path(path).name, path.strip()


def _load_manifest(path: Path, *, limit: int | None = None) -> list[BenchmarkSample]:
    samples: list[BenchmarkSample] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            item = json.loads(line)
            audio_path = Path(item["audio"])
            if not audio_path.is_absolute():
                audio_path = path.parent / audio_path
            terms = tuple(str(term) for term in item.get("terms", []) if str(term).strip())
            samples.append(
                BenchmarkSample(
                    sample_id=str(item.get("id") or audio_path.stem),
                    audio_path=audio_path,
                    reference=str(item.get("reference") or ""),
                    terms=terms,
                    slice_name=str(item.get("slice") or "general").strip() or "general",
                    weight=max(0.0, float(item.get("weight", 1.0))),
                    expected_silence=bool(item.get("expected_silence", False)),
                    reference_source=str(item.get("reference_source") or ""),
                    reference_kind=str(item.get("reference_kind") or ""),
                    independent_audio=item.get("independent_audio", (item.get("augmentation") or {}).get("independent_hours", True)) is True,
                )
            )
            if limit and len(samples) >= limit:
                break
    return samples


def _samples_from_files(paths: list[str], *, limit: int | None = None) -> list[BenchmarkSample]:
    samples: list[BenchmarkSample] = []
    for raw_path in paths:
        matches = [Path(item) for item in glob.glob(raw_path)] if any(mark in raw_path for mark in "*?[]") else [Path(raw_path)]
        for path in sorted(matches):
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(path)
            samples.append(BenchmarkSample(sample_id=path.stem, audio_path=path.resolve()))
            if limit and len(samples) >= limit:
                return samples
    return samples


def _term_stats(text: str, terms: tuple[str, ...]) -> dict[str, Any]:
    hits = [term for term in terms if term and term in text]
    missed = [term for term in terms if term and term not in text]
    return {
        "term_count": len(terms),
        "term_hits": hits,
        "missed_terms": missed,
    }


def _transcribe_sample(
    model: WhisperModel,
    sample: BenchmarkSample,
    *,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
) -> dict[str, Any]:
    duration = _duration_seconds(sample.audio_path)
    started = time.perf_counter()
    segments, info = model.transcribe(
        str(sample.audio_path),
        language=language,
        task="transcribe",
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    segment_list = []
    first_segment_seconds = None
    for segment in segments:
        if first_segment_seconds is None:
            first_segment_seconds = time.perf_counter() - started
        segment_list.append(segment)
    transcript = "".join(segment.text for segment in segment_list).strip()
    elapsed = time.perf_counter() - started
    cer = _char_error_rate(sample.reference, transcript)
    wer = _word_error_rate(sample.reference, transcript)
    mer = _mixed_error_rate(sample.reference, transcript)
    term_stats = _term_stats(transcript, sample.terms)
    return {
        "id": sample.sample_id,
        "audio": str(sample.audio_path),
        "duration_seconds": round(duration, 3),
        "transcribe_seconds": round(elapsed, 3),
        "rtf": round(elapsed / duration, 4) if duration else None,
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "segment_count": len(segment_list),
        "first_segment_seconds": None if first_segment_seconds is None else round(first_segment_seconds, 3),
        "slice": sample.slice_name,
        "weight": sample.weight,
        "expected_silence": sample.expected_silence,
        "silence_hallucination": sample.expected_silence and bool(_normalize_text(transcript)),
        "reference": sample.reference,
        "transcript": transcript,
        "char_error_rate": None if cer is None else round(cer, 4),
        "word_error_rate": None if wer is None else round(wer, 4),
        "mixed_error_rate": None if mer is None else round(mer, 4),
        **term_stats,
    }


def _transcribe_sensevoice_sample(
    provider: LocalSenseVoiceProvider,
    sample: BenchmarkSample,
) -> dict[str, Any]:
    duration = _duration_seconds(sample.audio_path)
    started = time.perf_counter()
    result = provider.transcribe(
        str(sample.audio_path),
        capture_id=f"benchmark-{sample.sample_id}",
        output_folder=sample.audio_path.parent,
    )
    elapsed = time.perf_counter() - started
    transcript = result.transcript_text.strip()
    cer = _char_error_rate(sample.reference, transcript)
    wer = _word_error_rate(sample.reference, transcript)
    mer = _mixed_error_rate(sample.reference, transcript)
    return {
        "id": sample.sample_id,
        "audio": str(sample.audio_path),
        "duration_seconds": round(duration, 3),
        "transcribe_seconds": round(elapsed, 3),
        "rtf": round(elapsed / duration, 4) if duration else None,
        "language": result.language,
        "segment_count": result.segment_count,
        "first_segment_seconds": round(elapsed, 3),
        "slice": sample.slice_name,
        "weight": sample.weight,
        "expected_silence": sample.expected_silence,
        "silence_hallucination": sample.expected_silence and bool(_normalize_text(transcript)),
        "reference": sample.reference,
        "transcript": transcript,
        "char_error_rate": None if cer is None else round(cer, 4),
        "word_error_rate": None if wer is None else round(wer, 4),
        "mixed_error_rate": None if mer is None else round(mer, 4),
        **_term_stats(transcript, sample.terms),
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [item for item in results if not item.get("error_code")]
    rtfs = [item["rtf"] for item in successful if isinstance(item.get("rtf"), float)]
    cers = [item["char_error_rate"] for item in results if isinstance(item.get("char_error_rate"), float)]
    wers = [item["word_error_rate"] for item in results if isinstance(item.get("word_error_rate"), float)]
    mers = [
        item.get("mixed_error_rate", item.get("char_error_rate"))
        for item in results
        if isinstance(item.get("mixed_error_rate", item.get("char_error_rate")), float)
    ]
    weighted_cers = [
        (item["char_error_rate"], float(item.get("weight", 1.0)))
        for item in results
        if isinstance(item.get("char_error_rate"), float) and float(item.get("weight", 1.0)) > 0
    ]
    weighted_mers = [
        (item.get("mixed_error_rate", item.get("char_error_rate")), float(item.get("weight", 1.0)))
        for item in results
        if isinstance(item.get("mixed_error_rate", item.get("char_error_rate")), float)
        and float(item.get("weight", 1.0)) > 0
    ]
    term_count = sum(int(item.get("term_count", 0)) for item in results)
    term_hits = sum(len(item.get("term_hits", [])) for item in results)
    first_segments = [item["first_segment_seconds"] for item in results if isinstance(item.get("first_segment_seconds"), float)]
    slice_cers: dict[str, list[float]] = {}
    slice_mers: dict[str, list[float]] = {}
    for item in results:
        if isinstance(item.get("char_error_rate"), float):
            slice_cers.setdefault(str(item.get("slice") or "general"), []).append(item["char_error_rate"])
        mixed_value = item.get("mixed_error_rate", item.get("char_error_rate"))
        if isinstance(mixed_value, float):
            slice_mers.setdefault(str(item.get("slice") or "general"), []).append(mixed_value)
    unique_samples: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(results):
        unique_samples.setdefault(str(item.get("id") or index), item)
    independent_samples = {key: value for key, value in unique_samples.items() if value.get("independent_audio", True) is True}
    run_count = max((int(item.get("repeat") or 1) for item in results), default=0)
    return {
        "sample_count": len(independent_samples),
        "result_count": len(results),
        "run_count": run_count,
        "audio_hours": round(sum(float(item.get("duration_seconds") or 0) for item in independent_samples.values()) / 3600, 3),
        "avg_rtf": round(statistics.mean(rtfs), 4) if rtfs else None,
        "p50_rtf": None if not rtfs else round(_percentile(rtfs, 0.50) or 0, 4),
        "p95_rtf": None if not rtfs else round(_percentile(rtfs, 0.95) or 0, 4),
        "avg_char_error_rate": round(statistics.mean(cers), 4) if cers else None,
        "avg_word_error_rate": round(statistics.mean(wers), 4) if wers else None,
        "avg_mixed_error_rate": round(statistics.mean(mers), 4) if mers else None,
        "weighted_char_error_rate": (
            round(sum(cer * weight for cer, weight in weighted_cers) / sum(weight for _, weight in weighted_cers), 4)
            if weighted_cers
            else None
        ),
        "weighted_mixed_error_rate": (
            round(sum(error * weight for error, weight in weighted_mers) / sum(weight for _, weight in weighted_mers), 4)
            if weighted_mers
            else None
        ),
        "term_recall": round(term_hits / term_count, 4) if term_count else None,
        "p95_first_segment_seconds": (
            None if not first_segments else round(_percentile(first_segments, 0.95) or 0, 3)
        ),
        "silence_hallucination_count": sum(bool(item.get("silence_hallucination")) for item in results),
        "slice_char_error_rate": {
            name: round(statistics.mean(values), 4) for name, values in sorted(slice_cers.items())
        },
        "slice_mixed_error_rate": {
            name: round(statistics.mean(values), 4) for name, values in sorted(slice_mers.items())
        },
        "failure_count": len(results) - len(successful),
        "failure_rate": round((len(results) - len(successful)) / len(results), 4) if results else 0.0,
        "has_reference": bool(cers),
    }


def _hardware_profile() -> dict[str, Any]:
    gpu: list[str] = []
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if output.returncode == 0:
            gpu = [line.strip() for line in output.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "gpu": gpu,
    }


def _release_dataset_ready(summary: dict[str, Any]) -> bool:
    required_slices = {
        "mandarin",
        "dialect",
        "mixed_language",
        "english",
        "multi_speaker",
        "noise",
        "music",
        "proper_nouns",
        "long_form",
    }
    return (
        int(summary.get("sample_count") or 0) >= 72
        and float(summary.get("audio_hours") or 0) >= 4.0
        and int(summary.get("run_count") or 0) >= 3
        and float(summary.get("failure_rate") or 0) == 0
        and required_slices.issubset(set((summary.get("slice_mixed_error_rate") or {}).keys()))
    )


def _candidate_decisions(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not runs:
        return []
    baseline = runs[0]["summary"]
    baseline_cer = baseline.get("weighted_mixed_error_rate", baseline.get("weighted_char_error_rate"))
    baseline_rtf = baseline.get("p95_rtf")
    decisions = []
    for run in runs[1:]:
        summary = run["summary"]
        candidate_cer = summary.get("weighted_mixed_error_rate", summary.get("weighted_char_error_rate"))
        candidate_rtf = summary.get("p95_rtf")
        quality_gain = (
            (baseline_cer - candidate_cer) / baseline_cer
            if isinstance(baseline_cer, float) and baseline_cer > 0 and isinstance(candidate_cer, float)
            else None
        )
        speed_gain = (
            (baseline_rtf - candidate_rtf) / baseline_rtf
            if isinstance(baseline_rtf, float) and baseline_rtf > 0 and isinstance(candidate_rtf, float)
            else None
        )
        critical_regression = 0.0
        baseline_slices = baseline.get("slice_mixed_error_rate", baseline.get("slice_char_error_rate")) or {}
        candidate_slices = summary.get("slice_mixed_error_rate", summary.get("slice_char_error_rate")) or {}
        for name, base_value in baseline_slices.items():
            candidate_value = candidate_slices.get(name)
            if isinstance(base_value, float) and isinstance(candidate_value, float):
                critical_regression = max(critical_regression, candidate_value - base_value)
        absolute_quality_change = (
            candidate_cer - baseline_cer
            if isinstance(baseline_cer, float) and isinstance(candidate_cer, float)
            else None
        )
        release_ready = bool(summary.get("release_dataset_ready"))
        stable = float(summary.get("failure_rate") or 0) == 0
        peak_gpu_memory_mb = summary.get("peak_gpu_memory_mb")
        model_size_bytes = summary.get("model_size_bytes")
        resource_fit = (
            isinstance(peak_gpu_memory_mb, (int, float))
            and peak_gpu_memory_mb <= 5200
            and isinstance(model_size_bytes, int)
            and model_size_bytes <= 2_000_000_000
        )
        quality_path = quality_gain is not None and quality_gain >= 0.08
        speed_path = (
            speed_gain is not None
            and speed_gain >= 0.25
            and absolute_quality_change is not None
            and absolute_quality_change <= 0.005
        )
        eligible = (
            release_ready
            and stable
            and resource_fit
            and critical_regression <= 0.01
            and (quality_path or speed_path)
        )
        reasons = []
        if not release_ready:
            reasons.append("dataset_gate_not_met")
        if not stable:
            reasons.append("failures_detected")
        if not resource_fit:
            reasons.append("resource_budget_not_verified")
        if critical_regression > 0.01:
            reasons.append("critical_slice_regression")
        if not (quality_path or speed_path):
            reasons.append("quality_or_speed_gate_not_met")
        decisions.append(
            {
                "model": run["model"],
                "eligible_to_replace_default": eligible,
                "quality_gain": None if quality_gain is None else round(quality_gain, 4),
                "speed_gain": None if speed_gain is None else round(speed_gain, 4),
                "absolute_quality_change": None if absolute_quality_change is None else round(absolute_quality_change, 4),
                "max_slice_regression": round(critical_regression, 4),
                "reasons": reasons,
            }
        )
    return decisions


def _progress(message: str) -> None:
    """Keep long local benchmark runs visibly alive without dumping transcripts."""
    print(f"[transcription-benchmark] {message}", file=sys.stderr, flush=True)


class _BenchmarkCheckpoint:
    """Append complete results durably; resume only the same experiment snapshot."""
    def __init__(self, path: Path | None, identity: dict):
        self.path, self.results = path, {}
        if path is None:
            return
        identity = json.loads(json.dumps(identity, ensure_ascii=True))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines or json.loads(lines[0]) != {"identity": identity}:
                raise ValueError("Benchmark checkpoint belongs to a different experiment; use a new checkpoint path.")
            for line in lines[1:]:
                item = json.loads(line)
                self.results[tuple(item["key"])] = item["result"]
        else:
            self._append({"identity": identity})

    def _append(self, item: dict):
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(item, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def record(self, key: tuple, result: dict):
        if self.path is not None:
            self._append({"key": key, "result": result})
            self.results[key] = result


def _benchmark_identity(args, samples) -> dict:
    def digest(path):
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    model_hashes = {}
    for spec in [*args.model, *args.sensevoice_model, *getattr(args, "sensevoice_cuda_model", [])]:
        _, value = _parse_model(spec)
        path = Path(value)
        if not path.is_dir():
            raise ValueError("Resumable benchmarks require local, fixed model directories.")
        for file in sorted(path.rglob("*")):
            if file.is_file():
                model_hashes[str(file.resolve())] = digest(file)
    return {"options": {key: value for key, value in vars(args).items() if key not in {"json_out", "checkpoint"}},
            "code": {str(path.relative_to(PROJECT_ROOT)): digest(path) for path in
                     (Path(__file__), PROJECT_ROOT/"app/providers/transcription.py", PROJECT_ROOT/"app/sensevoice_cuda.py", PROJECT_ROOT/"scripts/sensevoice_gpu_worker.py")},
            "models": model_hashes,
            "samples": [{"id": item.sample_id, "audio_sha256": digest(item.audio_path), "reference": item.reference,
                         "slice": item.slice_name, "terms": item.terms, "weight": item.weight,
                         "reference_kind": item.reference_kind, "reference_source": item.reference_source,
                         "independent_audio": item.independent_audio,
                         "expected_silence": item.expected_silence} for item in samples]}


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    requested_language = str(args.language or "").strip().lower()
    language = None if requested_language in {"", "auto", "detect", "none"} else requested_language
    samples = _load_manifest(Path(args.manifest), limit=args.limit) if args.manifest else _samples_from_files(args.files, limit=args.limit)
    if not samples:
        raise SystemExit("No samples found")

    checkpoint_path = getattr(args, "checkpoint", None)
    checkpoint = _BenchmarkCheckpoint(Path(checkpoint_path) if checkpoint_path else None,
                                      _benchmark_identity(args, samples) if checkpoint_path else {})

    model_specs = [_parse_model(item) for item in args.model]
    sensevoice_specs = [(label, path, "cpu") for label, path in map(_parse_model, args.sensevoice_model)]
    sensevoice_specs += [(label, path, "cuda") for label, path in map(_parse_model, getattr(args, "sensevoice_cuda_model", []))]
    total_work = (len(model_specs) + len(sensevoice_specs)) * args.repeat * len(samples)
    completed_work = 0
    runs: list[dict[str, Any]] = []
    for label, model_path in model_specs:
        sampler = _ResourceSampler()
        sampler.start()
        started = time.perf_counter()
        sample_results: list[dict[str, Any]] = []
        model = None
        _progress(f"{label}: loading model")
        try:
            model = WhisperModel(model_path, device=args.device, compute_type=args.compute_type)
            load_seconds = time.perf_counter() - started
            _progress(f"{label}: model ready in {load_seconds:.2f}s")
            for repeat in range(1, args.repeat + 1):
                for sample in samples:
                    sample_started = time.perf_counter()
                    key = (label, model_path, args.device, repeat, sample.sample_id)
                    try:
                        result = checkpoint.results.get(key) or _transcribe_sample(
                            model,
                            sample,
                            language=language,
                            beam_size=args.beam_size,
                            vad_filter=args.vad_filter,
                        )
                        result["repeat"] = repeat
                    except Exception as exc:  # keep the report complete across candidate failures
                        result = _failed_sample(sample, exc, repeat=repeat)
                    if key not in checkpoint.results:
                        checkpoint.record(key, result)
                    sample_results.append(result)
                    completed_work += 1
                    status = "ok" if not result.get("error_code") else str(result.get("error_code"))
                    _progress(
                        f"{label}: {completed_work}/{total_work} "
                        f"repeat={repeat} sample={sample.sample_id} status={status} "
                        f"elapsed={time.perf_counter() - sample_started:.2f}s"
                    )
        except Exception as exc:
            load_seconds = time.perf_counter() - started
            _progress(f"{label}: model load failed ({type(exc).__name__})")
            for repeat in range(1, args.repeat + 1):
                for sample in samples:
                    sample_results.append(_failed_sample(sample, exc, repeat=repeat))
                    completed_work += 1
                    _progress(
                        f"{label}: {completed_work}/{total_work} "
                        f"repeat={repeat} sample={sample.sample_id} status=transcription_failed"
                    )
        finally:
            sampler.stop()
            # CTranslate2 owns GPU allocations through the model object. Release
            # this reference before measuring the next candidate, even on errors.
            model = None
            gc.collect()
        summary = _summarize(sample_results)
        summary["peak_gpu_memory_mb"] = (sampler.peak_gpu_memory_mb or None) if args.device == "cuda" and sampler.gpu_memory_complete else (None if args.device == "cuda" else 0)
        summary["model_size_bytes"] = _path_size_bytes(model_path)
        runs.append(
            {
                "model": label,
                "model_path": model_path,
                "device": args.device,
                "compute_type": args.compute_type,
                "beam_size": args.beam_size,
                "vad_filter": args.vad_filter,
                "language": args.language,
                "load_seconds": round(load_seconds, 3),
                "summary": summary,
                "samples": sample_results,
            }
        )
    for label, model_path, device in sensevoice_specs:
        sampler = _ResourceSampler()
        sampler.start()
        started = time.perf_counter()
        sample_results = []
        provider = None
        _progress(f"{label}: loading model")
        try:
            if device == "cuda":
                provider = LocalSenseVoiceCudaProvider(Path(model_path), Path(args.sensevoice_cuda_sdk))
                provider.worker.warm()
                sampler.add_process(provider.worker.process.pid)
            else:
                provider = LocalSenseVoiceProvider(model_path, num_threads=args.sensevoice_threads)
                provider._recognizer()
            load_seconds = time.perf_counter() - started
            _progress(f"{label}: model ready in {load_seconds:.2f}s")
            for repeat in range(1, args.repeat + 1):
                for sample in samples:
                    sample_started = time.perf_counter()
                    key = (label, model_path, device, repeat, sample.sample_id)
                    try:
                        result = checkpoint.results.get(key) or _transcribe_sensevoice_sample(provider, sample)
                        result["repeat"] = repeat
                    except Exception as exc:
                        result = _failed_sample(sample, exc, repeat=repeat)
                    if key not in checkpoint.results:
                        checkpoint.record(key, result)
                    sample_results.append(result)
                    completed_work += 1
                    status = "ok" if not result.get("error_code") else str(result.get("error_code"))
                    _progress(
                        f"{label}: {completed_work}/{total_work} "
                        f"repeat={repeat} sample={sample.sample_id} status={status} "
                        f"elapsed={time.perf_counter() - sample_started:.2f}s"
                    )
        except Exception as exc:
            load_seconds = time.perf_counter() - started
            _progress(f"{label}: model load failed ({type(exc).__name__})")
            for repeat in range(1, args.repeat + 1):
                for sample in samples:
                    sample_results.append(_failed_sample(sample, exc, repeat=repeat))
                    completed_work += 1
                    _progress(
                        f"{label}: {completed_work}/{total_work} "
                        f"repeat={repeat} sample={sample.sample_id} status=transcription_failed"
                    )
        finally:
            sampler.stop()
            if device == "cuda" and provider is not None:
                provider.worker.close()
        summary = _summarize(sample_results)
        summary["peak_gpu_memory_mb"] = (sampler.peak_gpu_memory_mb or None) if device == "cuda" and sampler.gpu_memory_complete else (None if device == "cuda" else 0)
        summary["model_size_bytes"] = _path_size_bytes(model_path)
        runs.append(
            {
                "model": label,
                "model_path": model_path,
                "engine": "sherpa_onnx_sensevoice",
                "device": device,
                "compute_type": "int8" if device == "cpu" and (Path(model_path) / "model.int8.onnx").is_file() else "float32",
                "beam_size": 1,
                "vad_filter": False,
                "language": "auto",
                "load_seconds": round(load_seconds, 3),
                "summary": summary,
                "samples": sample_results,
            }
        )
    sample_lookup = {sample.sample_id: sample for sample in samples}
    audio_hashes = {}
    for sample in samples:
        if sample.audio_path.is_file():
            with sample.audio_path.open("rb") as source:
                audio_hashes[sample.sample_id] = hashlib.file_digest(source, "sha256").hexdigest()
    for run in runs:
        for row in run["samples"]:
            sample = sample_lookup[row["id"]]
            row.update(audio_sha256=audio_hashes.get(sample.sample_id), reference_source=sample.reference_source, reference_kind=sample.reference_kind, independent_audio=sample.independent_audio)
        resource_measurements = {key: run["summary"][key] for key in ("peak_gpu_memory_mb", "model_size_bytes")}
        run["summary"] = {**_summarize(run["samples"]), **resource_measurements}
        run["summary"]["release_dataset_ready"] = _release_dataset_ready(run["summary"])
    return {
        "schema_version": 2,
        "hardware": _hardware_profile(),
        "benchmark_policy": {
            "minimum_unique_samples": 72,
            "minimum_audio_hours": 4.0,
            "required_runs": 3,
            "maximum_gpu_memory_mb": 5200,
            "maximum_model_size_bytes": 2_000_000_000,
        },
        "runs": runs,
        "candidate_decisions": _candidate_decisions(runs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local Whisper and SenseVoice with human references.")
    parser.add_argument("files", nargs="*", help="Audio/video files or glob patterns. Ignored when --manifest is set.")
    parser.add_argument("--manifest", help="JSONL manifest with audio, optional reference, and optional terms.")
    parser.add_argument("--model", action="append", default=None, help="Model spec as label=path_or_hf_id. Repeatable.")
    parser.add_argument("--sensevoice-model", action="append", default=[], help="SenseVoice model spec as label=directory. Repeatable.")
    parser.add_argument("--sensevoice-cuda-model", action="append", default=[], help="Isolated SenseVoice CUDA model as label=directory. Repeatable.")
    parser.add_argument("--sensevoice-cuda-sdk", default=str(PROJECT_ROOT / "workspace/runtime/candidates/sensevoice-cuda/site"))
    parser.add_argument("--sensevoice-threads", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--compute-type", default=COMPUTE_TYPE)
    parser.add_argument("--beam-size", type=int, default=BEAM_SIZE)
    parser.add_argument("--language", default=LANGUAGE)
    parser.add_argument("--vad-filter", action=argparse.BooleanOptionalAction, default=VAD_FILTER)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeat", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--json-out", help="Write the full benchmark result to this JSON file.")
    parser.add_argument("--checkpoint", help="Durable JSONL checkpoint; resumes only identical code, models, samples and options.")
    args = parser.parse_args()
    if args.model is None and not args.sensevoice_model and not args.sensevoice_cuda_model:
        args.model = [f"current={MODEL_PATH}"]
    elif args.model is None:
        args.model = []

    if not args.manifest and not args.files:
        parser.error("provide files or --manifest")

    result = run_benchmark(args)
    for run in result["runs"]:
        summary = run["summary"]
        cer = summary["avg_char_error_rate"]
        cer_label = "-" if cer is None else f"{cer:.4f}"
        print(
            f"{run['model']} | load {run['load_seconds']:.2f}s | "
            f"avg RTF {summary['avg_rtf']} | avg CER {cer_label} | "
            f"{run['device']}/{run['compute_type']} beam={run['beam_size']}"
        )
        for sample in run["samples"]:
            sample_cer = sample["char_error_rate"]
            sample_cer_label = "-" if sample_cer is None else f"{sample_cer:.4f}"
            transcript = re.sub(r"\s+", " ", str(sample.get("transcript") or "")).strip()
            if len(transcript) > 160:
                transcript = transcript[:157] + "..."
            print(
                f"  {sample['id']} | {sample.get('transcribe_seconds', 0):.2f}s | "
                f"RTF {sample['rtf']} | CER {sample_cer_label} | {transcript}"
            )

    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

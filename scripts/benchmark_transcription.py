from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
from faster_whisper import WhisperModel

from app.providers.transcription import LocalSenseVoiceProvider
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


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _char_error_rate(reference: str, hypothesis: str) -> float | None:
    reference = _normalize_text(reference)
    hypothesis = _normalize_text(hypothesis)
    if not reference:
        return None
    previous = list(range(len(hypothesis) + 1))
    for row, ref_char in enumerate(reference, start=1):
        current = [row]
        for col, hyp_char in enumerate(hypothesis, start=1):
            cost = 0 if ref_char == hyp_char else 1
            current.append(
                min(
                    previous[col] + 1,
                    current[col - 1] + 1,
                    previous[col - 1] + cost,
                )
            )
        previous = current
    return previous[-1] / len(reference)


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
    transcript = "".join(segment.text.strip() for segment in segment_list).strip()
    elapsed = time.perf_counter() - started
    cer = _char_error_rate(sample.reference, transcript)
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
        **_term_stats(transcript, sample.terms),
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    rtfs = [item["rtf"] for item in results if isinstance(item.get("rtf"), float)]
    cers = [item["char_error_rate"] for item in results if isinstance(item.get("char_error_rate"), float)]
    weighted_cers = [
        (item["char_error_rate"], float(item.get("weight", 1.0)))
        for item in results
        if isinstance(item.get("char_error_rate"), float) and float(item.get("weight", 1.0)) > 0
    ]
    term_count = sum(int(item.get("term_count", 0)) for item in results)
    term_hits = sum(len(item.get("term_hits", [])) for item in results)
    first_segments = [item["first_segment_seconds"] for item in results if isinstance(item.get("first_segment_seconds"), float)]
    slice_cers: dict[str, list[float]] = {}
    for item in results:
        if isinstance(item.get("char_error_rate"), float):
            slice_cers.setdefault(str(item.get("slice") or "general"), []).append(item["char_error_rate"])
    return {
        "sample_count": len(results),
        "audio_hours": round(sum(float(item.get("duration_seconds") or 0) for item in results) / 3600, 3),
        "avg_rtf": round(statistics.mean(rtfs), 4) if rtfs else None,
        "p95_rtf": None if not rtfs else round(_percentile(rtfs, 0.95) or 0, 4),
        "avg_char_error_rate": round(statistics.mean(cers), 4) if cers else None,
        "weighted_char_error_rate": (
            round(sum(cer * weight for cer, weight in weighted_cers) / sum(weight for _, weight in weighted_cers), 4)
            if weighted_cers
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
    required_slices = {"mandarin", "dialect", "mixed_language", "multi_speaker", "noise", "music", "long_form"}
    return (
        int(summary.get("sample_count") or 0) >= 60
        and float(summary.get("audio_hours") or 0) >= 3.0
        and required_slices.issubset(set((summary.get("slice_char_error_rate") or {}).keys()))
    )


def _candidate_decisions(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not runs:
        return []
    baseline = runs[0]["summary"]
    baseline_cer = baseline.get("weighted_char_error_rate")
    baseline_rtf = baseline.get("p95_rtf")
    decisions = []
    for run in runs[1:]:
        summary = run["summary"]
        candidate_cer = summary.get("weighted_char_error_rate")
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
        for name, base_value in (baseline.get("slice_char_error_rate") or {}).items():
            candidate_value = (summary.get("slice_char_error_rate") or {}).get(name)
            if base_value and isinstance(candidate_value, float):
                critical_regression = max(critical_regression, (candidate_value - base_value) / base_value)
        quality_tied = quality_gain is not None and quality_gain >= -0.02
        eligible = critical_regression <= 0.05 and (
            (quality_gain is not None and quality_gain >= 0.10)
            or (quality_tied and speed_gain is not None and speed_gain >= 0.30)
        )
        decisions.append(
            {
                "model": run["model"],
                "eligible_to_replace_default": eligible,
                "quality_gain": None if quality_gain is None else round(quality_gain, 4),
                "speed_gain": None if speed_gain is None else round(speed_gain, 4),
                "max_slice_regression": round(critical_regression, 4),
            }
        )
    return decisions


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    requested_language = str(args.language or "").strip().lower()
    language = None if requested_language in {"", "auto", "detect", "none"} else requested_language
    samples = _load_manifest(Path(args.manifest), limit=args.limit) if args.manifest else _samples_from_files(args.files, limit=args.limit)
    if not samples:
        raise SystemExit("No samples found")

    model_specs = [_parse_model(item) for item in args.model]
    runs: list[dict[str, Any]] = []
    for label, model_path in model_specs:
        started = time.perf_counter()
        model = WhisperModel(model_path, device=args.device, compute_type=args.compute_type)
        load_seconds = time.perf_counter() - started
        sample_results = [
            _transcribe_sample(
                model,
                sample,
                language=language,
                beam_size=args.beam_size,
                vad_filter=args.vad_filter,
            )
            for sample in samples
        ]
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
                "summary": _summarize(sample_results),
                "samples": sample_results,
            }
        )
    for label, model_path in [_parse_model(item) for item in args.sensevoice_model]:
        started = time.perf_counter()
        provider = LocalSenseVoiceProvider(model_path, num_threads=args.sensevoice_threads)
        provider._recognizer()
        load_seconds = time.perf_counter() - started
        sample_results = [_transcribe_sensevoice_sample(provider, sample) for sample in samples]
        runs.append(
            {
                "model": label,
                "model_path": model_path,
                "engine": "sherpa_onnx_sensevoice",
                "device": "cpu",
                "compute_type": "int8",
                "beam_size": 1,
                "vad_filter": False,
                "language": "auto",
                "load_seconds": round(load_seconds, 3),
                "summary": _summarize(sample_results),
                "samples": sample_results,
            }
        )
    for run in runs:
        run["summary"]["release_dataset_ready"] = _release_dataset_ready(run["summary"])
    return {"hardware": _hardware_profile(), "runs": runs, "candidate_decisions": _candidate_decisions(runs)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local Faster-Whisper transcription models.")
    parser.add_argument("files", nargs="*", help="Audio/video files or glob patterns. Ignored when --manifest is set.")
    parser.add_argument("--manifest", help="JSONL manifest with audio, optional reference, and optional terms.")
    parser.add_argument("--model", action="append", default=None, help="Model spec as label=path_or_hf_id. Repeatable.")
    parser.add_argument("--sensevoice-model", action="append", default=[], help="SenseVoice model spec as label=directory. Repeatable.")
    parser.add_argument("--sensevoice-threads", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--compute-type", default=COMPUTE_TYPE)
    parser.add_argument("--beam-size", type=int, default=BEAM_SIZE)
    parser.add_argument("--language", default=LANGUAGE)
    parser.add_argument("--vad-filter", action=argparse.BooleanOptionalAction, default=VAD_FILTER)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json-out", help="Write the full benchmark result to this JSON file.")
    args = parser.parse_args()
    if args.model is None and not args.sensevoice_model:
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
            print(
                f"  {sample['id']} | {sample['transcribe_seconds']:.2f}s | "
                f"RTF {sample['rtf']} | CER {sample_cer_label} | {sample['transcript']}"
            )

    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

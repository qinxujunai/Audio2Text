from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
from faster_whisper import WhisperModel

from scripts.config import BEAM_SIZE, COMPUTE_TYPE, DEVICE, LANGUAGE, MODEL_PATH, VAD_FILTER


@dataclass(frozen=True)
class BenchmarkSample:
    sample_id: str
    audio_path: Path
    reference: str = ""
    terms: tuple[str, ...] = ()


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
    segment_list = list(segments)
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
        "reference": sample.reference,
        "transcript": transcript,
        "char_error_rate": None if cer is None else round(cer, 4),
        **term_stats,
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    rtfs = [item["rtf"] for item in results if isinstance(item.get("rtf"), float)]
    cers = [item["char_error_rate"] for item in results if isinstance(item.get("char_error_rate"), float)]
    return {
        "sample_count": len(results),
        "avg_rtf": round(statistics.mean(rtfs), 4) if rtfs else None,
        "avg_char_error_rate": round(statistics.mean(cers), 4) if cers else None,
        "has_reference": bool(cers),
    }


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
    return {"runs": runs}


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local Faster-Whisper transcription models.")
    parser.add_argument("files", nargs="*", help="Audio/video files or glob patterns. Ignored when --manifest is set.")
    parser.add_argument("--manifest", help="JSONL manifest with audio, optional reference, and optional terms.")
    parser.add_argument("--model", action="append", default=None, help="Model spec as label=path_or_hf_id. Repeatable.")
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--compute-type", default=COMPUTE_TYPE)
    parser.add_argument("--beam-size", type=int, default=BEAM_SIZE)
    parser.add_argument("--language", default=LANGUAGE)
    parser.add_argument("--vad-filter", action=argparse.BooleanOptionalAction, default=VAD_FILTER)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json-out", help="Write the full benchmark result to this JSON file.")
    args = parser.parse_args()
    if args.model is None:
        args.model = [f"current={MODEL_PATH}"]

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

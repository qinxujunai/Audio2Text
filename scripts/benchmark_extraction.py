from __future__ import annotations

import argparse
import json
import multiprocessing
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Support both the documented module invocation and direct execution from the
# repository.  Python otherwise places only ``scripts/`` on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser_provider import browser_runtime_status
from app.resolver import infer_source_item_id, resolve_url
from app.source_adapters import get_url_adapter


@dataclass(frozen=True)
class ExtractionSample:
    sample_id: str
    url: str
    expected_platform: str = ""
    minimum_text_chars: int = 0
    minimum_images: int = 0
    require_media: bool = False


def _content_identity(platform: str, url: str) -> str | None:
    if platform == "wechat_article":
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if query.get("__biz") and query.get("mid"):
            values = [query["__biz"][0], query["mid"][0], (query.get("idx") or ["1"])[0]]
            return "wechat_article:" + ":".join(values)
        if parsed.path.startswith("/s/") and len(parsed.path) > 3:
            return "wechat_article:" + parsed.path[3:].rstrip("/")
        return None
    content_id = infer_source_item_id(platform, url)
    return f"{platform}:{content_id}" if content_id else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _load_manifest(path: Path) -> list[ExtractionSample]:
    samples: list[ExtractionSample] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            item = json.loads(line)
            samples.append(
                ExtractionSample(
                    sample_id=str(item.get("id") or f"sample-{len(samples) + 1}"),
                    url=str(item["url"]),
                    expected_platform=str(item.get("platform") or ""),
                    minimum_text_chars=max(0, int(item.get("minimum_text_chars") or 0)),
                    minimum_images=max(0, int(item.get("minimum_images") or 0)),
                    require_media=bool(item.get("require_media", False)),
                )
            )
    return samples


def _run_sample(sample: ExtractionSample, *, repeat: int, work_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    resolved = None
    try:
        resolved = resolve_url(sample.url)
        outcome = get_url_adapter(resolved).extract(resolved, work_dir / sample.sample_id)
        elapsed = time.perf_counter() - started
        text = "\n".join(
            value.strip()
            for value in (outcome.primary_text, outcome.article_text, outcome.notes_text)
            if value and value.strip()
        )
        complete = (
            (not sample.expected_platform or outcome.platform == sample.expected_platform)
            and len(text) >= sample.minimum_text_chars
            and len(outcome.image_urls) >= sample.minimum_images
            and (not sample.require_media or bool(outcome.media_file_path))
        )
        return {
            "id": sample.sample_id,
            "source_identity": _content_identity(resolved.platform, resolved.normalized_url),
            "repeat": repeat,
            "success": True,
            "complete": complete,
            "platform": outcome.platform,
            "duration_seconds": round(elapsed, 3),
            "text_chars": len(text),
            "image_count": len(outcome.image_urls),
            "media_available": bool(outcome.media_file_path),
            # Text first: the media here is the audio track; the original video is fetched after
            # the text is delivered, so this row does not time the video download.
            "video_deferred": bool(getattr(outcome, "deferred_video", None)),
            "needs_transcription": bool(outcome.needs_transcription),
            "subtitle_available": bool(outcome.subtitle_text or outcome.subtitle_timeline_text),
            "warning_count": len(outcome.warnings),
            "strategy": list(outcome.strategy),
            "fallback_used": bool(outcome.fallback_used),
            "fallback_chain": list(outcome.fallback_chain),
            "extractor_used": outcome.extractor_used,
            "attempt_count": max(1, len(outcome.fallback_chain)),
            "error_code": None,
        }
    except Exception as exc:  # report failures without aborting the 60-link run
        return {
            "id": sample.sample_id,
            "source_identity": _content_identity(resolved.platform, resolved.normalized_url) if resolved else _content_identity(sample.expected_platform, sample.url),
            "repeat": repeat,
            "success": False,
            "complete": False,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "text_chars": 0,
            "image_count": 0,
            "media_available": False,
            "needs_transcription": False,
            "subtitle_available": False,
            "warning_count": 0,
            "strategy": [],
            "fallback_used": False,
            "fallback_chain": [],
            "attempt_count": 0,
            "error_code": str(getattr(exc, "reason_code", type(exc).__name__)),
        }


def _sample_worker(
    sample: ExtractionSample,
    repeat: int,
    work_dir: str,
    result_connection: Any,
) -> None:
    """Run one external-link sample in a killable process boundary.

    Browser providers and third-party HTTP clients are synchronous and may
    outlive a thread timeout. A process boundary lets the benchmark stop one
    unresponsive provider without poisoning the remaining samples.
    """
    try:
        result_connection.send(_run_sample(sample, repeat=repeat, work_dir=Path(work_dir)))
    except Exception as exc:  # pragma: no cover - defensive child boundary
        result_connection.send(
            {
                "id": sample.sample_id,
                "repeat": repeat,
                "success": False,
                "complete": False,
                "duration_seconds": 0.0,
                "text_chars": 0,
                "image_count": 0,
                "media_available": False,
                "needs_transcription": False,
                "subtitle_available": False,
                "warning_count": 0,
                "strategy": [],
                "fallback_used": False,
                "fallback_chain": [],
                "attempt_count": 0,
                "error_code": type(exc).__name__,
            }
        )
    finally:
        result_connection.close()


def _run_sample_with_timeout(
    sample: ExtractionSample,
    *,
    repeat: int,
    work_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_sample_worker,
        args=(sample, repeat, str(work_dir), child_connection),
        daemon=True,
    )
    process.start()
    child_connection.close()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        parent_connection.close()
        process.close()
        return {
            "id": sample.sample_id,
            "repeat": repeat,
            "success": False,
            "complete": False,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "text_chars": 0,
            "image_count": 0,
            "media_available": False,
            "needs_transcription": False,
            "subtitle_available": False,
            "warning_count": 0,
            "strategy": [],
            "fallback_used": False,
            "fallback_chain": [],
            "attempt_count": 0,
            "error_code": "benchmark_timeout",
            "timeout_seconds": timeout_seconds,
        }
    try:
        if parent_connection.poll(2):
            return parent_connection.recv()
        return {
            "id": sample.sample_id,
            "repeat": repeat,
            "success": False,
            "complete": False,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "text_chars": 0,
            "image_count": 0,
            "media_available": False,
            "needs_transcription": False,
            "subtitle_available": False,
            "warning_count": 0,
            "strategy": [],
            "fallback_used": False,
            "fallback_chain": [],
            "attempt_count": 0,
            "error_code": "benchmark_worker_no_result",
        }
    except Exception:
        return {
            "id": sample.sample_id,
            "repeat": repeat,
            "success": False,
            "complete": False,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "text_chars": 0,
            "image_count": 0,
            "media_available": False,
            "needs_transcription": False,
            "subtitle_available": False,
            "warning_count": 0,
            "strategy": [],
            "fallback_used": False,
            "fallback_chain": [],
            "attempt_count": 0,
            "error_code": "benchmark_worker_no_result",
        }
    finally:
        parent_connection.close()
        process.close()


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(item["duration_seconds"]) for item in results]
    successes = [item for item in results if item["success"]]
    repeats_by_sample: dict[str, set[int]] = {}
    for item in results:
        repeats_by_sample.setdefault(str(item["id"]), set()).add(int(item["repeat"]))
    release_dataset_ready = len(repeats_by_sample) >= 60 and all(
        {1, 2, 3}.issubset(repeats) for repeats in repeats_by_sample.values()
    )
    return {
        "sample_count": len({str(item["id"]) for item in results}),
        "run_count": max((int(item["repeat"]) for item in results), default=0),
        "success_rate": round(len(successes) / len(results), 4) if results else 0.0,
        "content_complete_rate": round(sum(bool(item["complete"]) for item in results) / len(results), 4) if results else 0.0,
        "timeout_rate": round(
            sum(item.get("error_code") == "benchmark_timeout" for item in results) / len(results), 4
        ) if results else 0.0,
        "fallback_rate": round(sum(bool(item["fallback_used"]) for item in results) / len(results), 4) if results else 0.0,
        "p50_seconds": round(_percentile(durations, 0.5) or 0, 3) if durations else None,
        "p95_seconds": round(_percentile(durations, 0.95) or 0, 3) if durations else None,
        "mean_images": round(statistics.mean(float(item["image_count"]) for item in successes), 2) if successes else 0.0,
        "mean_text_chars": round(statistics.mean(float(item["text_chars"]) for item in successes), 2) if successes else 0.0,
        "media_complete_rate": round(
            sum(bool(item.get("media_available")) for item in successes) / len(successes), 4
        ) if successes else 0.0,
        "transcription_required_rate": round(
            sum(bool(item.get("needs_transcription")) for item in successes) / len(successes), 4
        ) if successes else 0.0,
        "release_dataset_ready": release_dataset_ready,
    }


def _replacement_decision(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    success_gain = float(candidate["success_rate"]) - float(baseline["success_rate"])
    p95_gain = (
        (float(baseline["p95_seconds"]) - float(candidate["p95_seconds"])) / float(baseline["p95_seconds"])
        if baseline.get("p95_seconds") and candidate.get("p95_seconds")
        else None
    )
    success_not_worse = float(candidate["success_rate"]) >= float(baseline["success_rate"])
    eligible = bool(candidate.get("release_dataset_ready")) and (
        success_gain >= 0.05 or (success_not_worse and p95_gain is not None and p95_gain >= 0.20)
    )
    return {
        "eligible_to_replace_default": eligible,
        "success_rate_gain": round(success_gain, 4),
        "p95_speed_gain": None if p95_gain is None else round(p95_gain, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark public-link extraction under one fixed manifest.")
    parser.add_argument("--manifest", required=True, help="JSONL with id, url, platform and completeness bounds.")
    parser.add_argument("--repeat", type=int, default=3, choices=range(1, 6))
    parser.add_argument(
        "--sample-timeout-seconds",
        type=float,
        default=180.0,
        help="Hard timeout for one public-link run; timed-out links are recorded and the batch continues.",
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--baseline-report", help="Optional prior report used for replacement eligibility.")
    args = parser.parse_args()

    samples = _load_manifest(Path(args.manifest))
    if not samples:
        raise SystemExit("No extraction samples found")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="wanxiang-extraction-benchmark-") as temp_dir:
        work_dir = Path(temp_dir)
        total = len(samples) * args.repeat
        completed = 0
        for repeat in range(1, args.repeat + 1):
            for sample in samples:
                result = _run_sample_with_timeout(
                    sample,
                    repeat=repeat,
                    work_dir=work_dir,
                    timeout_seconds=max(1.0, args.sample_timeout_seconds),
                )
                # Keep expected platform and canonical content identity even
                # for challenge/failure rows; failed observations still count.
                result.setdefault("source_identity", _content_identity(sample.expected_platform, sample.url))
                result.update(expected_platform=sample.expected_platform)
                result.setdefault("platform", sample.expected_platform)
                results.append(result)
                completed += 1
                print(
                    f"[{completed}/{total}] {sample.sample_id} run={repeat} "
                    f"success={result['success']} complete={result['complete']} "
                    f"seconds={result['duration_seconds']} error={result.get('error_code') or '-'}",
                    flush=True,
                )
    report: dict[str, Any] = {
        "schema_version": 1,
        "runtime": browser_runtime_status(),
        "summary": _summarize(results),
        "samples": results,
    }
    if args.baseline_report:
        baseline = json.loads(Path(args.baseline_report).read_text(encoding="utf-8"))["summary"]
        report["replacement_decision"] = _replacement_decision(baseline, report["summary"])
    output = Path(args.json_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

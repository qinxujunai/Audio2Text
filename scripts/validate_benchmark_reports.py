from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

REQUIRED_PLATFORMS = {"youtube", "bilibili", "xiaoyuzhou", "douyin", "xiaohongshu", "wechat_article"}
REQUIRED_SLICES = {"mandarin", "english", "mixed_language", "dialect", "multi_speaker", "noise", "music", "proper_nouns", "long_form"}


def _sample_evidence(rows: Any, *, transcription: bool) -> dict[str, Any]:
    reasons: set[str] = set()
    grouped: dict[str, list[dict]] = defaultdict(list)
    if not isinstance(rows, list) or not rows:
        return {"valid": False, "sample_count": 0, "audio_hours": 0.0, "run_count": 0, "reasons": ["raw_samples_missing"]}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            reasons.add("invalid_sample_row")
            continue
        grouped[row["id"]].append(row)
    identities: set[str] = set()
    independent_identities: set[str] = set()
    coverage: set[str] = set()
    seconds = 0.0
    rounds = []
    for records in grouped.values():
        repeats = [r.get("repeat") for r in records]
        if any(type(value) is not int or value < 1 for value in repeats) or len(set(repeats)) != len(repeats) or not {1, 2, 3}.issubset(set(repeats)):
            reasons.add("three_distinct_rounds_required_per_sample")
        rounds.append(len(set(value for value in repeats if type(value) is int)))
        first = records[0]
        identity_key = "audio_sha256" if transcription else "source_identity"
        identity = first.get(identity_key)
        if not isinstance(identity, str) or not identity or (transcription and re.fullmatch(r"[a-f0-9]{64}", identity) is None):
            reasons.add("sample_identity_missing")
        elif identity in identities:
            reasons.add("duplicate_sample_identity")
        else:
            identities.add(identity)
            if not transcription or first.get("independent_audio", True) is True:
                independent_identities.add(identity)
        if any(r.get(identity_key) != identity for r in records):
            reasons.add("sample_changed_between_rounds")
        if transcription:
            duration = first.get("duration_seconds")
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
                reasons.add("invalid_audio_duration")
            else:
                if first.get("independent_audio", True) is True:
                    seconds += duration
            for r in records:
                if r.get("error_code"):
                    reasons.add("transcription_failures_present")
                if r.get("reference_kind") != "human" or not r.get("reference_source") or not str(r.get("reference") or "").strip():
                    reasons.add("human_reference_required_for_every_sample")
                if any(r.get(k) != first.get(k) for k in ("duration_seconds", "reference", "reference_kind", "reference_source", "slice", "independent_audio")):
                    reasons.add("sample_changed_between_rounds")
                if not isinstance(r.get("mixed_error_rate"), (int, float)) or isinstance(r.get("mixed_error_rate"), bool) or not math.isfinite(r["mixed_error_rate"]) or r["mixed_error_rate"] < 0:
                    reasons.add("scored_reference_required")
                if r.get("slice") in REQUIRED_SLICES:
                    coverage.add(r["slice"])
        else:
            for r in records:
                if r.get("error_code") == "benchmark_timeout":
                    reasons.add("timeouts_present")
                if r.get("platform") in REQUIRED_PLATFORMS:
                    coverage.add(r["platform"])
                if r.get("platform") != first.get("platform"):
                    reasons.add("sample_changed_between_rounds")
    if len(independent_identities) < (72 if transcription else 60):
        reasons.add("insufficient_unique_samples")
    if transcription and seconds < 4 * 3600:
        reasons.add("insufficient_audio_hours")
    if not (REQUIRED_SLICES if transcription else REQUIRED_PLATFORMS).issubset(coverage):
        reasons.add("required_slices_missing" if transcription else "required_platforms_missing")
    return {"valid": not reasons, "sample_count": len(independent_identities), "audio_hours": round(seconds / 3600, 6), "run_count": min(rounds, default=0), "reasons": sorted(reasons)}


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark report must be an object: {path}")
    return payload


def validate_extraction_report(path: str | Path) -> dict[str, Any]:
    payload = _load(path)
    summary = payload.get("summary") or {}
    ready = bool(summary.get("release_dataset_ready"))
    evidence = _sample_evidence(payload.get("samples"), transcription=False)
    timeout_rate = float(summary.get("timeout_rate") or 0)
    result = {
        "kind": "extraction",
        "path": str(path),
        "ready": ready and timeout_rate == 0 and evidence["valid"],
        "sample_count": evidence["sample_count"],
        "run_count": evidence["run_count"],
        "success_rate": float(summary.get("success_rate") or 0),
        "p95_seconds": summary.get("p95_seconds"),
        "timeout_rate": timeout_rate,
        "reasons": evidence["reasons"],
    }
    if not ready:
        result["reasons"].append("release_dataset_ready=false")
    if timeout_rate != 0:
        result["reasons"].append("timeouts_present")
    return result


def validate_transcription_report(path: str | Path) -> dict[str, Any]:
    payload = _load(path)
    runs = payload.get("runs") or []
    run_results = []
    for run in runs:
        summary = run.get("summary") or {}
        evidence = _sample_evidence(run.get("samples"), transcription=True)
        run_results.append(
            {
                "model": run.get("model"),
                "ready": bool(summary.get("release_dataset_ready"))
                and float(summary.get("failure_rate") or 0) == 0 and evidence["valid"],
                "sample_count": evidence["sample_count"],
                "run_count": evidence["run_count"],
                "audio_hours": evidence["audio_hours"],
                "reasons": evidence["reasons"],
                "failure_rate": float(summary.get("failure_rate") or 0),
                "p95_rtf": summary.get("p95_rtf"),
            }
        )
    return {
        "kind": "transcription",
        "path": str(path),
        "ready": bool(run_results) and all(item["ready"] for item in run_results),
        "runs": run_results,
        "reasons": [] if run_results and all(item["ready"] for item in run_results) else ["every model run must pass the dataset gate"],
    }


def validate_reports(extraction_path: str | Path, transcription_path: str | Path) -> dict[str, Any]:
    extraction = validate_extraction_report(extraction_path)
    transcription = validate_transcription_report(transcription_path)
    return {
        "schema_version": 1,
        "passed": bool(extraction["ready"] and transcription["ready"]),
        "extraction": extraction,
        "transcription": transcription,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate release benchmark evidence before final deployment.")
    parser.add_argument("--extraction", required=True)
    parser.add_argument("--transcription", required=True)
    args = parser.parse_args()
    report = validate_reports(args.extraction, args.transcription)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

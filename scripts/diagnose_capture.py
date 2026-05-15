from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.capture_diagnostics import read_trace_records
from app.settings import ARTIFACTS_DIR, CAPTURES_DIR


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _duration(start: str | None, end: str | None) -> str:
    started_at = _parse_time(start)
    ended_at = _parse_time(end)
    if started_at is None or ended_at is None:
        return "unknown"
    return f"{max((ended_at - started_at).total_seconds(), 0.0):.0f}s"


def _artifact_rows(capture_id: str, capture: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    artifacts = (capture.get("result") or {}).get("artifacts") or []
    artifact_dir = ARTIFACTS_DIR / capture_id
    for artifact in artifacts:
        file_name = artifact.get("file_name") or ""
        file_path = artifact_dir / file_name
        size = file_path.stat().st_size if file_name and file_path.exists() else artifact.get("size_bytes")
        rows.append(f"- {artifact.get('type', 'unknown')}: {file_name or artifact.get('url', '')} ({size or 0} bytes)")
    return rows


def _trace_rows(records: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for record in records:
        parts = [
            record.get("at", ""),
            str(record.get("event", "")),
            str(record.get("stage", "") or ""),
        ]
        if "duration_seconds" in record:
            parts.append(f"{record['duration_seconds']}s")
        if record.get("provider"):
            parts.append(f"provider={record['provider']}")
        if record.get("reason_code"):
            parts.append(f"reason={record['reason_code']}")
        rows.append("- " + " | ".join(part for part in parts if part))
    return rows


def _processing_trace_rows(events: list[dict[str, Any]]) -> tuple[list[str], float]:
    rows: list[str] = []
    recovery_seconds = 0.0
    previous_time: datetime | None = None
    for event in events:
        event_time = _parse_time(event.get("at"))
        if event_time is not None and previous_time is not None:
            if event.get("stage") == "queued" and "恢复" in str(event.get("message", "")):
                recovery_seconds += max((event_time - previous_time).total_seconds(), 0.0)
        if event_time is not None:
            previous_time = event_time
        parts = [
            event.get("at", ""),
            str(event.get("stage", "") or ""),
            str(event.get("level", "") or ""),
            str(event.get("provider", "") or ""),
            str(event.get("message", "") or ""),
            str(event.get("detail", "") or ""),
        ]
        rows.append("- " + " | ".join(part for part in parts if part))
    return rows, recovery_seconds


def diagnose(capture_id: str) -> int:
    capture_path = CAPTURES_DIR / capture_id / "capture.json"
    if not capture_path.exists():
        print(f"Capture not found: {capture_id}")
        return 2

    capture = _read_json(capture_path)
    source = capture.get("source") or {}
    processing = capture.get("processing") or {}
    records = read_trace_records(capture_id)
    final_record = next((item for item in reversed(records) if item.get("event") == "capture_finished"), None)

    print(f"Capture: {capture_id}")
    print(f"Status: {capture.get('status')}")
    print(f"Title: {capture.get('title') or ''}")
    print(f"Platform: {source.get('platform') or capture.get('source_platform') or ''}")
    print(f"Content type: {source.get('content_type') or capture.get('content_type') or ''}")
    print(f"Created: {capture.get('created_at') or ''}")
    print(f"Started: {capture.get('started_at') or ''}")
    print(f"Finished: {capture.get('finished_at') or ''}")
    print(f"Wall duration: {_duration(capture.get('started_at'), capture.get('finished_at'))}")
    if capture.get("error_stage") or capture.get("error_message"):
        print(f"Error: {capture.get('error_stage') or ''} | {capture.get('error_message') or ''}")
    if processing.get("failure_reason_code"):
        print(f"Failure reason: {processing.get('failure_reason_code')}")
    if final_record:
        print(f"Trace total: {final_record.get('total_seconds', 'unknown')}s")
        print(f"Recovery: {final_record.get('recovery_seconds', 0)}s")
        print(f"Stage durations: {final_record.get('stage_durations', {})}")

    print("\nArtifacts:")
    artifact_rows = _artifact_rows(capture_id, capture)
    print("\n".join(artifact_rows) if artifact_rows else "- none")

    print("\nTrace records:")
    trace_rows = _trace_rows(records)
    print("\n".join(trace_rows) if trace_rows else "- no capture trace JSONL found")

    processing_events = processing.get("trace_events") or []
    if processing_events:
        rows, recovery_seconds = _processing_trace_rows(processing_events)
        print("\nProcessing trace events:")
        if recovery_seconds:
            print(f"Worker recovery gap: {recovery_seconds:.0f}s")
        print("\n".join(rows))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a capture's stage trace and artifacts.")
    parser.add_argument("capture_id", help="Capture id, for example 10dbf56819a9")
    args = parser.parse_args()
    return diagnose(args.capture_id)


if __name__ == "__main__":
    raise SystemExit(main())

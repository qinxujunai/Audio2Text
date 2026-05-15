from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.store_fs import now_iso
from scripts.config import LOGS_DIR


TRACE_DIR = LOGS_DIR / "capture_traces"


def _safe_detail(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_detail(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_detail(item) for key, item in value.items()}
    return str(value)


class CaptureDiagnostics:
    """Small per-capture JSONL trace writer for production debugging."""

    def __init__(self, capture_id: str) -> None:
        self.capture_id = capture_id
        self.path = TRACE_DIR / f"{capture_id}.jsonl"
        self._stage_starts: dict[str, float] = {}
        self.stage_durations: dict[str, float] = {}
        self._started_at = time.perf_counter()

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": now_iso(),
            "capture_id": self.capture_id,
            **{key: _safe_detail(value) for key, value in payload.items()},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def event(self, event: str, *, stage: str | None = None, **fields: Any) -> None:
        self._write({"event": event, "stage": stage, **fields})

    def start(self, stage: str, **fields: Any) -> None:
        self._stage_starts[stage] = time.perf_counter()
        self.event("stage_start", stage=stage, **fields)

    def finish(self, stage: str, **fields: Any) -> float:
        started_at = self._stage_starts.pop(stage, None)
        duration_seconds = 0.0 if started_at is None else max(time.perf_counter() - started_at, 0.0)
        self.stage_durations[stage] = self.stage_durations.get(stage, 0.0) + duration_seconds
        self.event("stage_end", stage=stage, duration_seconds=round(duration_seconds, 3), **fields)
        return duration_seconds

    def fail(self, stage: str, **fields: Any) -> None:
        started_at = self._stage_starts.pop(stage, None)
        duration_seconds = 0.0 if started_at is None else max(time.perf_counter() - started_at, 0.0)
        if started_at is not None:
            self.stage_durations[stage] = self.stage_durations.get(stage, 0.0) + duration_seconds
        self.event("stage_failed", stage=stage, duration_seconds=round(duration_seconds, 3), **fields)

    def finish_capture(self, *, status: str, recovery_seconds: float = 0.0, **fields: Any) -> None:
        total_seconds = max(time.perf_counter() - self._started_at, 0.0)
        self.event(
            "capture_finished",
            stage="done" if status == "done" else "failed",
            status=status,
            total_seconds=round(total_seconds, 3),
            recovery_seconds=round(max(recovery_seconds, 0.0), 3),
            stage_durations={key: round(value, 3) for key, value in sorted(self.stage_durations.items())},
            **fields,
        )

    def summary_text(self, *, recovery_seconds: float = 0.0) -> str:
        parts = [f"total={sum(self.stage_durations.values()) + max(recovery_seconds, 0.0):.1f}s"]
        for stage in ("resolve", "extract", "transcribe", "compose", "artifact"):
            if stage in self.stage_durations:
                parts.append(f"{stage}={self.stage_durations[stage]:.1f}s")
        if recovery_seconds > 0:
            parts.append(f"recovery={recovery_seconds:.1f}s")
        return " ".join(parts)


def write_capture_event(capture_id: str, event: str, *, stage: str | None = None, **fields: Any) -> None:
    CaptureDiagnostics(capture_id).event(event, stage=stage, **fields)


def read_trace_records(capture_id: str, *, logs_dir: Path | None = None) -> list[dict[str, Any]]:
    trace_path = (logs_dir or LOGS_DIR) / "capture_traces" / f"{capture_id}.jsonl"
    if not trace_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"event": "invalid_jsonl", "raw": line})
    return records

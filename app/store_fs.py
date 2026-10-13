from __future__ import annotations

import json
import os
import shutil
import time
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.schemas import CaptureModel, ProcessingStateModel, SourceMetaModel
from app.settings import ARTIFACTS_DIR, CAPTURES_DIR, CAPTURE_HISTORY_LIMIT


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CaptureStoreUnavailableError(RuntimeError):
    """Raised when capture storage is temporarily unavailable for safe read/write."""


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()

# A capture is read repeatedly while the worker is writing it.  Windows
# security/indexing software can briefly deny access to the atomically replaced
# JSON file.  Keep the last complete in-process snapshot as a read-only
# fallback so a polling request can return the previous state instead of
# turning a transient file lock into a user-visible 503.  Read-modify-write
# callers explicitly disable this fallback below; stale data must never be
# used as the basis for a write.
_LAST_GOOD_JSON: dict[str, dict] = {}
_LAST_GOOD_JSON_GUARD = threading.Lock()

# Windows security/indexing software can briefly hold an atomically replaced
# JSON file.  A short retry is safe here because every write uses a unique
# temporary file and ``os.replace``; the longer budget prevents a transient
# file lock from becoming a user-visible capture interruption.
_STORE_WRITE_ATTEMPTS = 32
_STORE_READ_ATTEMPTS = 32
_STORE_RETRY_DELAY_CAP_SECONDS = 0.25
_RECOVERY_SNAPSHOT_SUFFIX = ".lastgood"


def _store_retry_delay(attempt: int) -> float:
    return min(0.05 * (attempt + 1), _STORE_RETRY_DELAY_CAP_SECONDS)


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@contextmanager
def _locked_path(path: Path):
    lock = _path_lock(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def capture_workspace(capture_id: str) -> Path:
    target = CAPTURES_DIR / capture_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _capture_dir(capture_id: str) -> Path:
    return CAPTURES_DIR / capture_id


def _capture_path(capture_id: str) -> Path:
    return _capture_dir(capture_id) / "capture.json"


def _recovery_snapshot_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{_RECOVERY_SNAPSHOT_SUFFIX}")


def _write_recovery_snapshot(path: Path, serialized: str) -> None:
    """Best-effort disk snapshot used only after the primary write succeeds."""
    snapshot_path = _recovery_snapshot_path(path)
    temp_path = snapshot_path.with_suffix(f"{snapshot_path.suffix}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(serialized, encoding="utf-8")
        os.replace(temp_path, snapshot_path)
    except OSError:
        # The primary capture file is authoritative.  A recovery snapshot must
        # never turn a successful write into a failed capture update.
        pass
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_recovery_snapshot(path: Path) -> dict | None:
    snapshot_path = _recovery_snapshot_path(path)
    if not snapshot_path.exists():
        return None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_path = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    last_error: OSError | None = None
    replaced = False
    with _locked_path(path):
        try:
            temp_path.write_text(serialized, encoding="utf-8")
            for attempt in range(_STORE_WRITE_ATTEMPTS):
                try:
                    os.replace(temp_path, path)
                    replaced = True
                    break
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(_store_retry_delay(attempt))
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    if replaced:
        _write_recovery_snapshot(path, serialized)
        with _LAST_GOOD_JSON_GUARD:
            _LAST_GOOD_JSON[str(path.resolve())] = dict(payload)
        return
    raise CaptureStoreUnavailableError(f"Unable to safely write capture store file: {path}") from last_error


def _read_json(path: Path, *, allow_stale: bool = True) -> dict | None:
    if not path.exists():
        return None
    last_error: Exception | None = None
    for attempt in range(_STORE_READ_ATTEMPTS):
        try:
            with _locked_path(path):
                payload = json.loads(path.read_text(encoding="utf-8"))
            with _LAST_GOOD_JSON_GUARD:
                _LAST_GOOD_JSON[str(path.resolve())] = dict(payload)
            # Older captures predate the disk-level recovery file.  Seed it
            # lazily on the first successful read so a process restart does
            # not reopen the old "busy file -> 503" window.
            recovery_path = _recovery_snapshot_path(path)
            if not recovery_path.exists():
                _write_recovery_snapshot(
                    path,
                    json.dumps(payload, ensure_ascii=False, indent=2),
                )
            return payload
        except FileNotFoundError:
            return None
        except (PermissionError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(_store_retry_delay(attempt))
    if allow_stale:
        with _LAST_GOOD_JSON_GUARD:
            cached = _LAST_GOOD_JSON.get(str(path.resolve()))
        if cached is not None:
            return dict(cached)
        recovery_payload = _read_recovery_snapshot(path)
        if recovery_payload is not None:
            with _LAST_GOOD_JSON_GUARD:
                _LAST_GOOD_JSON[str(path.resolve())] = dict(recovery_payload)
            return recovery_payload
    if last_error is not None:
        raise CaptureStoreUnavailableError(f"Unable to safely read capture store file: {path}") from last_error
    return None


def _forget_cached_json(path: Path) -> None:
    with _LAST_GOOD_JSON_GUARD:
        _LAST_GOOD_JSON.pop(str(path.resolve()), None)


def _capture_sort_key(capture: CaptureModel) -> tuple[str, str]:
    return (
        capture.updated_at or capture.finished_at or capture.created_at or "",
        capture.id,
    )


def _delete_capture_storage(capture: CaptureModel | None, capture_dir: Path) -> None:
    storage_path = (capture.storage_path or "").strip() if capture else ""
    if not storage_path:
        return

    target = Path(storage_path)
    if not target.exists():
        return

    try:
        resolved_target = target.resolve()
        resolved_capture_dir = capture_dir.resolve()
    except OSError:
        resolved_target = target
        resolved_capture_dir = capture_dir

    if resolved_target == resolved_capture_dir or resolved_capture_dir in resolved_target.parents:
        return

    try:
        target.unlink(missing_ok=True)
    except OSError:
        return

    parent = target.parent
    try:
        if parent.exists() and parent != capture_dir and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return


def _delete_capture_files(capture_id: str) -> None:
    capture_dir = _capture_dir(capture_id)
    artifact_dir = ARTIFACTS_DIR / capture_id
    _forget_cached_json(_capture_path(capture_id))
    capture = get_capture(capture_id)
    _delete_capture_storage(capture, capture_dir)
    if capture_dir.exists():
        shutil.rmtree(capture_dir, ignore_errors=True)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir, ignore_errors=True)


def delete_capture(capture_id: str) -> bool:
    capture = get_capture(capture_id)
    if capture is None:
        return False
    _delete_capture_files(capture_id)
    return True


def prune_capture_history(limit: int = CAPTURE_HISTORY_LIMIT) -> None:
    if limit <= 0:
        return

    captures = list_captures(limit=5000)
    completed = [item for item in captures if item.status in {"done", "failed", "needs_user_action"}]
    completed.sort(key=_capture_sort_key, reverse=True)
    stale = completed[limit:]

    for capture in stale:
        _delete_capture_files(capture.id)


def clear_completed_captures() -> int:
    cleared = 0
    for capture in list_captures(limit=5000):
        if capture.status in {"done", "failed", "needs_user_action"}:
            _delete_capture_files(capture.id)
            cleared += 1
    return cleared


def init_db() -> None:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    prune_capture_history()


def create_capture(
    *,
    input_type: str,
    source_platform: str,
    content_type: str,
    url: str | None = None,
    file_name: str | None = None,
    storage_path: str | None = None,
    client_ip: str | None = None,
) -> CaptureModel:
    prune_capture_history()
    capture_id = uuid.uuid4().hex[:12]
    created_at = now_iso()
    capture = CaptureModel(
        id=capture_id,
        input_type=input_type,
        status="queued",
        title=file_name or url,
        source_platform=source_platform,
        content_type=content_type,
        url=url,
        file_name=file_name,
        storage_path=storage_path,
        created_at=created_at,
        updated_at=created_at,
        client_ip=client_ip,
        source=SourceMetaModel(platform=source_platform, content_type=content_type, canonical_url=url),
        processing=ProcessingStateModel(),
        result=None,
    )
    _write_json(_capture_path(capture_id), capture.model_dump(mode="json"))
    prune_capture_history()
    return capture


def update_capture(capture_id: str, **updates) -> CaptureModel | None:
    path = _capture_path(capture_id)
    # Keep the read-modify-write transaction under one per-capture lock.  A
    # worker progress update can overlap with image preparation or a user
    # retry; locking only _write_json() lets the later writer overwrite fields
    # read from an older snapshot.
    with _locked_path(path):
        current = get_capture(capture_id, allow_stale=False)
        if current is None:
            return None

        data = current.model_dump(mode="json")
        normalized_updates = _normalize_capture_updates(updates)
        for key, value in normalized_updates.items():
            if key in {"source", "processing", "result"} and value is not None:
                data[key] = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            else:
                data[key] = value
        # Normalize the merged record as well as the incoming patch.  A legacy
        # capture may already be persisted as ``failed`` and receive a later
        # progress/title/artifact update without an explicit status field.
        # Normalizing only ``updates`` would let that old terminal state leak
        # back to disk.
        data = _normalize_capture_updates(data)
        data["updated_at"] = now_iso()

        updated = CaptureModel.model_validate(data)
        _write_json(path, updated.model_dump(mode="json"))
    if updated.status in {"done", "failed", "needs_user_action"}:
        prune_capture_history()
    return updated


def _normalize_capture_updates(updates: dict) -> dict:
    """Keep whole-capture terminal state actionable at the storage boundary.

    ``failed`` remains readable for old records and artifact-level statuses, but
    a newly persisted Capture must never become a dead-end terminal record. This
    guard protects callers outside the main pipeline (recovery jobs, desktop
    sidecar, and future adapters) from reintroducing the old state machine.
    """
    normalized = dict(updates)
    if normalized.get("status") != "failed":
        return normalized

    normalized["status"] = "needs_user_action"
    if normalized.get("result_state") == "failed":
        normalized["result_state"] = "awaiting_user"

    processing = normalized.get("processing")
    if processing is not None:
        if hasattr(processing, "model_copy"):
            processing_updates = {
                "progress_percent": max(18, getattr(processing, "progress_percent", 0) or 0),
            }
            if getattr(processing, "current_stage", None) == "failed":
                processing_updates["current_stage"] = "awaiting_user"
            processing = processing.model_copy(update=processing_updates)
        elif isinstance(processing, dict):
            processing = dict(processing)
            if processing.get("current_stage") == "failed":
                processing["current_stage"] = "awaiting_user"
            processing["progress_percent"] = max(18, processing.get("progress_percent", 0) or 0)
        normalized["processing"] = processing
    return normalized


def get_capture(capture_id: str, *, allow_stale: bool = True) -> CaptureModel | None:
    payload = _read_json(_capture_path(capture_id), allow_stale=allow_stale)
    if payload is None:
        return None
    # Older workers could persist a whole-capture ``failed`` record before the
    # actionable state was introduced.  Normalize on read as well as on write
    # so queue recovery, history, and non-HTTP callers cannot observe or
    # propagate that dead-end state.
    return CaptureModel.model_validate(_normalize_capture_updates(payload))


def list_captures(limit: int = CAPTURE_HISTORY_LIMIT, client_ip: str | None = None) -> list[CaptureModel]:
    captures: list[CaptureModel] = []
    for record_path in CAPTURES_DIR.glob("*/capture.json"):
        try:
            payload = _read_json(record_path)
            if payload is None:
                continue
            capture = CaptureModel.model_validate(_normalize_capture_updates(payload))
            if client_ip and capture.client_ip and capture.client_ip != client_ip:
                continue
            captures.append(capture)
        except Exception:
            continue
    captures.sort(key=lambda item: item.created_at, reverse=True)
    return captures[:limit]


def find_capture_by_url(url: str, *, statuses: tuple[str, ...] = ("done", "processing", "queued")) -> CaptureModel | None:
    normalized = (url or "").strip()
    if not normalized:
        return None

    for capture in list_captures(limit=max(CAPTURE_HISTORY_LIMIT * 2, 200)):
        if capture.input_type != "url":
            continue
        if capture.status not in statuses:
            continue
        if capture.url == normalized or capture.source.canonical_url == normalized:
            return capture
    return None


def find_capture_by_source_identity(
    platform: str,
    source_item_id: str,
    *,
    statuses: tuple[str, ...] = ("done", "processing", "queued"),
) -> CaptureModel | None:
    normalized_platform = (platform or "").strip()
    normalized_id = (source_item_id or "").strip()
    if not normalized_platform or not normalized_id:
        return None

    for capture in list_captures(limit=max(CAPTURE_HISTORY_LIMIT * 2, 200)):
        if capture.status not in statuses:
            continue
        if capture.source_platform != normalized_platform:
            continue
        stored_identity = capture.source.source_item_id or (capture.result.source_item_id if capture.result else "")
        if stored_identity == normalized_id:
            return capture
    return None


def list_pending_capture_ids() -> list[str]:
    return [
        capture.id
        for capture in list_captures(limit=max(CAPTURE_HISTORY_LIMIT * 2, 200))
        if capture.status in {"queued", "processing"}
    ]

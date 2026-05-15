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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_path = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    last_error: OSError | None = None
    with _locked_path(path):
        try:
            temp_path.write_text(serialized, encoding="utf-8")
            for attempt in range(16):
                try:
                    os.replace(temp_path, path)
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    raise CaptureStoreUnavailableError(f"Unable to safely write capture store file: {path}") from last_error


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    last_error: Exception | None = None
    for attempt in range(10):
        try:
            with _locked_path(path):
                return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (PermissionError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise CaptureStoreUnavailableError(f"Unable to safely read capture store file: {path}") from last_error
    return None


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
    completed = [item for item in captures if item.status in {"done", "failed"}]
    completed.sort(key=_capture_sort_key, reverse=True)
    stale = completed[limit:]

    for capture in stale:
        _delete_capture_files(capture.id)


def clear_completed_captures() -> int:
    cleared = 0
    for capture in list_captures(limit=5000):
        if capture.status in {"done", "failed"}:
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
    current = get_capture(capture_id)
    if current is None:
        return None

    data = current.model_dump(mode="json")
    for key, value in updates.items():
        if key in {"source", "processing", "result"} and value is not None:
            data[key] = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        else:
            data[key] = value
    data["updated_at"] = now_iso()

    updated = CaptureModel.model_validate(data)
    _write_json(_capture_path(capture_id), updated.model_dump(mode="json"))
    if updated.status in {"done", "failed"}:
        prune_capture_history()
    return updated


def get_capture(capture_id: str) -> CaptureModel | None:
    payload = _read_json(_capture_path(capture_id))
    if payload is None:
        return None
    return CaptureModel.model_validate(payload)


def list_captures(limit: int = CAPTURE_HISTORY_LIMIT, client_ip: str | None = None) -> list[CaptureModel]:
    captures: list[CaptureModel] = []
    for record_path in CAPTURES_DIR.glob("*/capture.json"):
        try:
            payload = _read_json(record_path)
            if payload is None:
                continue
            capture = CaptureModel.model_validate(payload)
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

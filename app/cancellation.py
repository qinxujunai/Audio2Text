"""Let a person stop a capture that is still being processed.

A cancel request is recorded per capture. The worker notices it at its next
progress update (``raise_if_cancelled``); a yt-dlp download running in a child
process is terminated at once so a long download does not keep going.
``CaptureCancelled`` derives from BaseException on purpose: the many
``except Exception`` fallbacks (engine fallback, automatic retries) must not
swallow it and carry on.
"""

from __future__ import annotations

import contextvars
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

CURRENT_CAPTURE: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_capture", default=None)

_lock = threading.Lock()
_requested: set[str] = set()
_processes: dict[str, dict[subprocess.Popen, Callable[[subprocess.Popen], None]]] = {}


class CaptureCancelled(BaseException):
    """Raised inside the worker when the person cancelled this capture."""


def request_cancel(capture_id: str) -> None:
    with _lock:
        _requested.add(capture_id)
        processes = list(_processes.get(capture_id, {}).items())
    for process, stop in processes:
        _stop(process, stop)


def is_cancelled(capture_id: str | None) -> bool:
    if not capture_id:
        return False
    with _lock:
        return capture_id in _requested


def clear(capture_id: str) -> None:
    with _lock:
        _requested.discard(capture_id)
        _processes.pop(capture_id, None)


def raise_if_cancelled(capture_id: str | None = None) -> None:
    if is_cancelled(capture_id or CURRENT_CAPTURE.get()):
        raise CaptureCancelled()


@contextmanager
def working_on(capture_id: str) -> Iterator[None]:
    token = CURRENT_CAPTURE.set(capture_id)
    try:
        yield
    finally:
        CURRENT_CAPTURE.reset(token)


@contextmanager
def tracked_process(
    process: subprocess.Popen,
    stop: Callable[[subprocess.Popen], None] | None = None,
) -> Iterator[None]:
    """Remember a child process of the current capture so a cancel can stop it."""
    capture_id = CURRENT_CAPTURE.get()
    stopper = stop or _terminate
    if capture_id:
        with _lock:
            _processes.setdefault(capture_id, {})[process] = stopper
            cancelled = capture_id in _requested
        if cancelled:
            _stop(process, stopper)
    try:
        yield
    finally:
        if capture_id:
            with _lock:
                _processes.get(capture_id, {}).pop(process, None)


def _stop(process: subprocess.Popen, stop: Callable[[subprocess.Popen], None]) -> None:
    try:
        if process.poll() is None:
            stop(process)
    except OSError:
        pass


def _terminate(process: subprocess.Popen) -> None:
    process.terminate()

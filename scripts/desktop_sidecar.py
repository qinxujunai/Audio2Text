from __future__ import annotations

import ctypes
import os
import sys
import threading
import time



def _windows_process_is_alive(pid: int) -> bool:
    synchronize = 0x00100000
    wait_timeout = 0x00000102
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _watch_desktop_parent() -> None:
    raw_pid = os.getenv("AUDIO2TEXT_DESKTOP_PARENT_PID", "").strip()
    if not raw_pid or os.name != "nt":
        return
    try:
        parent_pid = int(raw_pid)
    except ValueError:
        return

    def watch() -> None:
        while _windows_process_is_alive(parent_pid):
            time.sleep(0.1)
        os._exit(0)

    threading.Thread(target=watch, name="desktop-parent-watch", daemon=True).start()


if __name__ == "__main__":
    if "--sensevoice-gpu-worker" in sys.argv:
        from scripts.sensevoice_gpu_worker import main as gpu_worker_main
        raise SystemExit(gpu_worker_main())
    if "--ytdlp-worker" in sys.argv:
        from scripts.ytdlp_media_worker import main as ytdlp_worker_main

        raise SystemExit(ytdlp_worker_main())
    _watch_desktop_parent()
    from scripts.start_api import main
    raise SystemExit(main())

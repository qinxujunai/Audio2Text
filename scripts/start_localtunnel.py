from __future__ import annotations

import re
import shutil
import subprocess
import sys
import urllib.request

from scripts.config import API_PORT, PROJECT_ROOT


def _check_local_api(url: str) -> None:
    try:
        with urllib.request.urlopen(url + "/health", timeout=5) as response:
            if response.status >= 400:
                raise RuntimeError(f"health returned HTTP {response.status}")
    except Exception as exc:  # noqa: BLE001 - surface a friendly deployment hint.
        raise RuntimeError(
            f"Local API is not reachable at {url}/health. "
            "Start it first with: .\\.venv\\Scripts\\python.exe -m scripts.start_api"
        ) from exc


def _npx_command() -> str:
    for candidate in ("npx.cmd", "npx"):
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("npx is not installed. Install Node.js first, or use Cloudflare Tunnel.")


def main() -> int:
    local_url = f"http://127.0.0.1:{API_PORT}"
    _check_local_api(local_url)

    print(f"[INFO] Project directory: {PROJECT_ROOT}", flush=True)
    print(f"[INFO] Local URL: {local_url}", flush=True)
    print("[INFO] Starting localtunnel via npx. Keep this window open while sharing the link.", flush=True)

    process = subprocess.Popen(
        [
            _npx_command(),
            "--yes",
            "localtunnel",
            "--port",
            str(API_PORT),
            "--local-host",
            "127.0.0.1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    url_pattern = re.compile(r"https://[a-zA-Z0-9.-]+\.loca\.lt")
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            match = url_pattern.search(line)
            if match:
                print("\n[PUBLIC URL]", flush=True)
                print(match.group(0), flush=True)
                print("\n[INFO] Share this URL. Press Ctrl+C to stop the tunnel.", flush=True)
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

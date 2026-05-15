from __future__ import annotations

import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from scripts.config import API_PORT, PROJECT_ROOT, RUNTIME_DIR


CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
)


def _cloudflared_path() -> Path:
    if sys.platform.startswith("win"):
        return RUNTIME_DIR / "cloudflared" / "cloudflared.exe"
    return RUNTIME_DIR / "cloudflared" / "cloudflared"


def _download_cloudflared(target: Path) -> None:
    if not sys.platform.startswith("win"):
        raise RuntimeError(
            "Automatic cloudflared download is only wired for Windows. "
            "Install cloudflared manually, then rerun this script."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Downloading cloudflared to {target}", flush=True)
    with urllib.request.urlopen(CLOUDFLARED_URL, timeout=120) as response:
        target.write_bytes(response.read())


def _ensure_cloudflared() -> Path:
    bundled = _cloudflared_path()
    if bundled.exists():
        return bundled

    for candidate in ("cloudflared.exe", "cloudflared"):
        try:
            completed = subprocess.run(
                ["where" if sys.platform.startswith("win") else "which", candidate],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except OSError:
            continue
        first_line = completed.stdout.splitlines()[0].strip() if completed.stdout else ""
        if completed.returncode == 0 and first_line:
            return Path(first_line)

    _download_cloudflared(bundled)
    return bundled


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


def main() -> int:
    port = API_PORT
    local_url = f"http://localhost:{port}"
    _check_local_api(local_url)

    cloudflared = _ensure_cloudflared()
    print(f"[INFO] Project directory: {PROJECT_ROOT}", flush=True)
    print(f"[INFO] Local URL: {local_url}", flush=True)
    print("[INFO] Starting Cloudflare Quick Tunnel. Keep this window open while sharing the link.", flush=True)
    print("[INFO] Waiting for trycloudflare.com URL...", flush=True)

    process = subprocess.Popen(
        [str(cloudflared), "tunnel", "--protocol", "http2", "--url", local_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    url_pattern = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")
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

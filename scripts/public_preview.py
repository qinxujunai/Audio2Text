from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from scripts.config import API_PORT, PROJECT_ROOT
from scripts.start_cloudflare_tunnel import _ensure_cloudflared
from scripts.start_localtunnel import _npx_command


PREVIEW_DIR = PROJECT_ROOT / "tests_runtime" / "public_preview"
CURRENT_URL_FILE = PREVIEW_DIR / "current_url.txt"
STATUS_FILE = PREVIEW_DIR / "last_status.json"
EXPECTED_PRODUCT_FEATURE_NAME = "万象成文"
LEGACY_APP_TITLES = {"Praxis AI Capture", "Praxis AI｜无界笃行 · 万象成文"}


@dataclass(slots=True)
class TunnelResult:
    provider: str
    url: str
    process: subprocess.Popen[str]
    status: str = "started"


def _http_ok(url: str, *, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except Exception:
        return False


def _is_current_product_payload(payload: dict) -> bool:
    return (
        payload.get("product_feature_name") == EXPECTED_PRODUCT_FEATURE_NAME
        or payload.get("feature_name") == EXPECTED_PRODUCT_FEATURE_NAME
        or payload.get("product_name") == EXPECTED_PRODUCT_FEATURE_NAME
        or payload.get("app_title") in LEGACY_APP_TITLES
    )


def _api_is_current_product(base_url: str, *, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/config", timeout=timeout) as response:
            if response.status >= 400:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return _is_current_product_payload(payload)


def _api_ready(base_url: str, *, timeout: float = 5.0) -> bool:
    return _http_ok(base_url.rstrip("/") + "/health", timeout=timeout) and _api_is_current_product(
        base_url,
        timeout=timeout,
    )


def _wait_for_health(base_url: str, *, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _http_ok(base_url.rstrip("/") + "/health", timeout=3):
            return True
        time.sleep(0.6)
    return False


def _wait_for_api(base_url: str, *, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _api_ready(base_url, timeout=3):
            return True
        time.sleep(0.6)
    return False


def _copy_to_clipboard(text: str) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        subprocess.run("clip", input=text, text=True, check=False)
    except OSError:
        return


def _write_status(payload: dict) -> None:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if payload.get("url"):
        CURRENT_URL_FILE.write_text(str(payload["url"]).strip() + "\n", encoding="utf-8")


def _start_api_if_needed(local_url: str) -> subprocess.Popen[str] | None:
    if _api_ready(local_url, timeout=2.0):
        print("[INFO] 本地服务已经在运行。", flush=True)
        return None
    if _http_ok(local_url.rstrip("/") + "/health", timeout=2.0):
        raise RuntimeError(
            f"{local_url} 已有服务在运行，但不是当前万象成文 API。请先关闭占用 8000 的旧服务，"
            "或设置 AUDIO2TEXT_API_PORT 使用其他端口。"
        )

    print("[INFO] 正在启动本地服务，请稍等...", flush=True)
    process = subprocess.Popen(
        [str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "scripts.start_api"]
        if sys.platform.startswith("win")
        else [sys.executable, "-m", "scripts.start_api"],
        cwd=PROJECT_ROOT,
    )
    if not _wait_for_api(local_url, timeout=60.0):
        process.terminate()
        raise RuntimeError("本地服务没有成功启动。请先检查 start_api.bat 窗口中的错误。")
    print("[INFO] 本地服务已启动。", flush=True)
    return process


def _read_url_from_process(
    process: subprocess.Popen[str],
    pattern: re.Pattern[str],
    *,
    timeout: float = 45.0,
) -> str:
    assert process.stdout is not None
    deadline = time.time() + timeout
    lines: list[str] = []
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            time.sleep(0.2)
            continue
        print(line, end="", flush=True)
        lines.append(line)
        match = pattern.search(line)
        if match:
            return match.group(0)
    raise RuntimeError("没有等到公网链接输出。\n" + "".join(lines[-20:]))


def _start_cloudflare(local_url: str) -> TunnelResult:
    process = subprocess.Popen(
        [str(_ensure_cloudflared()), "tunnel", "--protocol", "http2", "--url", local_url],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    url = _read_url_from_process(process, re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com"))
    return TunnelResult(provider="cloudflare", url=url, process=process)


def _start_localtunnel(local_url: str) -> TunnelResult:
    process = subprocess.Popen(
        [_npx_command(), "--yes", "localtunnel", "--port", str(API_PORT), "--local-host", "127.0.0.1"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    url = _read_url_from_process(process, re.compile(r"https://[a-zA-Z0-9.-]+\.loca\.lt"))
    return TunnelResult(provider="localtunnel", url=url, process=process)


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    local_url = f"http://127.0.0.1:{API_PORT}"
    api_process: subprocess.Popen[str] | None = None
    tunnel: TunnelResult | None = None
    try:
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        api_process = _start_api_if_needed(local_url)

        print("[INFO] 正在生成公网链接，优先尝试 Cloudflare。", flush=True)
        try:
            tunnel = _start_cloudflare(local_url)
            if not _wait_for_health(tunnel.url, timeout=18.0):
                raise RuntimeError("Cloudflare 链接生成了，但公网健康检查没有通过。")
        except Exception as exc:
            print(f"[WARN] Cloudflare 暂不可用：{exc}", flush=True)
            if tunnel:
                _stop_process(tunnel.process)
            print("[INFO] 正在回退到 localtunnel。", flush=True)
            tunnel = _start_localtunnel(local_url)
            if not _wait_for_health(tunnel.url, timeout=30.0):
                raise RuntimeError("localtunnel 链接生成了，但公网健康检查没有通过。")

        _copy_to_clipboard(tunnel.url)
        _write_status(
            {
                "provider": tunnel.provider,
                "url": tunnel.url,
                "local_url": local_url,
                "status": "ok",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        print("\n[PUBLIC URL]", flush=True)
        print(tunnel.url, flush=True)
        print("\n[INFO] 链接已写入 tests_runtime/public_preview/current_url.txt，并已尝试复制到剪贴板。", flush=True)
        print("[INFO] 电脑、这个窗口和本地 API 都要保持开启。按 Ctrl+C 可停止公网预览。", flush=True)
        if tunnel.provider == "localtunnel":
            print("[INFO] localtunnel 首次访问会出现确认页，把页面显示的 IP 填进去点 Continue 即可。", flush=True)

        assert tunnel.process.stdout is not None
        for line in tunnel.process.stdout:
            print(line, end="", flush=True)
        return tunnel.process.wait()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        _write_status({"status": "failed", "error": str(exc), "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if tunnel:
            _stop_process(tunnel.process)
        _stop_process(api_process)


if __name__ == "__main__":
    raise SystemExit(main())

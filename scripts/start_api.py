from __future__ import annotations

import socket
import subprocess
import sys
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from app.runtime_preflight import ensure_runtime_ready
from scripts.config import API_HOST, API_PORT, DEPLOYMENT_MODE


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_MANIFEST = PROJECT_ROOT / "frontend" / "dist" / "build-manifest.json"
FRONTEND_INDEX = PROJECT_ROOT / "frontend" / "dist" / "index.html"
FRONTEND_FINGERPRINT_SCRIPT = PROJECT_ROOT / "web" / "scripts" / "frontend_fingerprint.mjs"
FRONTEND_SOURCE_DIR = PROJECT_ROOT / "web"


@dataclass(frozen=True)
class PortOwner:
    pid: str
    command: str = ""


class PortInUseError(RuntimeError):
    pass


class FrontendBuildError(RuntimeError):
    pass


def _run_frontend_fingerprint(node: str) -> dict[str, object]:
    completed = subprocess.run(
        [node, str(FRONTEND_FINGERPRINT_SCRIPT), "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FrontendBuildError("前端源码指纹输出无效，拒绝启动旧版界面。") from exc
    if not isinstance(payload, dict) or not payload.get("source_fingerprint"):
        raise FrontendBuildError("前端源码指纹缺失，拒绝启动旧版界面。")
    return payload


def _read_frontend_manifest() -> dict[str, object]:
    if not FRONTEND_MANIFEST.is_file() or not FRONTEND_INDEX.is_file():
        raise FrontendBuildError(
            "frontend/dist 缺少构建清单或入口文件，请先运行 `cd web; npm run build`。"
        )
    try:
        payload = json.loads(FRONTEND_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrontendBuildError("前端构建清单无法读取，拒绝启动旧版界面。") from exc
    if not isinstance(payload, dict):
        raise FrontendBuildError("前端构建清单格式无效，拒绝启动旧版界面。")
    return payload


def _frontend_manifest_matches(source_fingerprint: str) -> tuple[bool, str]:
    manifest = _read_frontend_manifest()
    if manifest.get("schema_version") != 1:
        return False, "构建清单版本不受支持"
    if manifest.get("source_fingerprint") != source_fingerprint:
        return False, "源码指纹与构建产物不一致"
    outputs = manifest.get("output_files")
    if not isinstance(outputs, list) or not outputs:
        return False, "构建清单没有资源清单"
    dist_root = FRONTEND_MANIFEST.parent.resolve()
    for raw_output in outputs:
        output = str(raw_output)
        candidate = (dist_root / output).resolve()
        if dist_root not in candidate.parents or not candidate.is_file():
            return False, f"构建资源缺失: {output}"
    return True, str(manifest.get("build_id") or "unknown")


def _ensure_frontend_build_current() -> None:
    """Never serve a stale SPA when the direct Python entrypoint is used."""
    if getattr(sys, "frozen", False):
        return

    node = shutil.which("node")
    if not node:
        # Cloud images build web/ and frontend/dist together in the Docker
        # builder stage. The slim runtime intentionally has no Node.js, so
        # the manifest is trusted only for that immutable deployment mode.
        if DEPLOYMENT_MODE == "cloud_preview" and FRONTEND_MANIFEST.is_file() and FRONTEND_INDEX.is_file():
            return
        if FRONTEND_MANIFEST.exists() or FRONTEND_INDEX.exists():
            raise FrontendBuildError(
                "检测到前端构建产物，但系统没有 Node.js，无法验证它是否对应当前源码；"
                "为避免启动旧 UI，请安装 Node.js 或先在有 Node.js 的环境执行 `cd web; npm run build`。"
            )
        raise FrontendBuildError(
            "前端构建产物不存在且系统没有 Node.js，请先安装 Node.js 并执行 `cd web; npm run build`。"
        )

    current = _run_frontend_fingerprint(node)
    source_fingerprint = str(current["source_fingerprint"])
    try:
        matches, detail = _frontend_manifest_matches(source_fingerprint)
    except FrontendBuildError:
        matches, detail = False, "构建清单缺失或无效"
    if matches:
        return

    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise FrontendBuildError(
            f"前端构建过期（{detail}），但系统没有 npm；已拒绝启动旧 UI。"
        )
    completed = subprocess.run(
        [npm, "run", "build"],
        cwd=FRONTEND_SOURCE_DIR,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise FrontendBuildError(
            f"前端构建过期（{detail}），自动构建失败（退出码 {completed.returncode}）；已拒绝启动旧 UI。"
        )
    try:
        matches, detail = _frontend_manifest_matches(source_fingerprint)
    except FrontendBuildError as exc:
        raise FrontendBuildError(f"前端自动构建后仍无法验证：{exc}") from exc
    if not matches:
        raise FrontendBuildError(f"前端自动构建后仍然过期（{detail}），已拒绝启动旧 UI。")


def _socket_family(host: str) -> socket.AddressFamily:
    return socket.AF_INET6 if ":" in (host or "") else socket.AF_INET


def _assert_port_available(host: str, port: int) -> None:
    try:
        with socket.socket(_socket_family(host), socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
    except OSError as exc:
        owners = _find_port_owners(port)
        owner_lines = "\n".join(
            f"  - PID {owner.pid}: {owner.command or '未能读取进程命令'}" for owner in owners
        )
        if not owner_lines:
            owner_lines = "  - 未能读取占用进程，请用 netstat / Get-Process 手动检查。"
        raise PortInUseError(
            "\n".join(
                [
                    f"端口已被占用，无法启动当前服务: {host}:{port}",
                    "占用进程:",
                    owner_lines,
                    "请先关闭旧服务，或临时设置 AUDIO2TEXT_API_PORT 使用其他端口。",
                    "例如 PowerShell: $env:AUDIO2TEXT_API_PORT='8001'; .\\.venv\\Scripts\\python.exe -m scripts.start_api",
                ]
            )
        ) from exc


def _find_port_owners(port: int) -> list[PortOwner]:
    if sys.platform.startswith("win"):
        return _find_port_owners_windows(port)
    return _find_port_owners_unix(port)


def _find_port_owners_windows(port: int) -> list[PortOwner]:
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return []

    pids: list[str] = []
    suffix = f":{port}"
    for raw_line in completed.stdout.splitlines():
        parts = raw_line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[-2].upper()
        pid = parts[-1]
        if state == "LISTENING" and local_address.endswith(suffix) and pid not in pids:
            pids.append(pid)

    return [PortOwner(pid=pid, command=_windows_process_command(pid)) for pid in pids]


def _windows_process_command(pid: str) -> str:
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return " ".join(completed.stdout.split())


def _find_port_owners_unix(port: int) -> list[PortOwner]:
    commands = (
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        ["ss", "-ltnp", f"sport = :{port}"],
    )
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except OSError:
            continue
        owners = _parse_unix_port_owners(completed.stdout)
        if owners:
            return owners
    return []


def _parse_unix_port_owners(output: str) -> list[PortOwner]:
    owners: list[PortOwner] = []
    seen: set[str] = set()
    for raw_line in output.splitlines():
        if "LISTEN" not in raw_line.upper():
            continue
        pid = ""
        command = raw_line.strip()
        if "pid=" in raw_line:
            pid = raw_line.split("pid=", 1)[1].split(",", 1)[0].strip()
        else:
            parts = raw_line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                pid = parts[1]
        if not pid or pid in seen:
            continue
        seen.add(pid)
        owners.append(PortOwner(pid=pid, command=command))
    return owners


def main() -> int:
    try:
        _assert_port_available(API_HOST, API_PORT)
    except PortInUseError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        _ensure_frontend_build_current()
    except FrontendBuildError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    ensure_runtime_ready()
    application = "app.main:app"
    if getattr(sys, "frozen", False):
        from app.main import app as packaged_application

        application = packaged_application
    uvicorn.run(
        application,
        host=API_HOST,
        port=API_PORT,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

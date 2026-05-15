from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass

import uvicorn

from app.runtime_preflight import ensure_runtime_ready
from scripts.config import API_HOST, API_PORT


@dataclass(frozen=True)
class PortOwner:
    pid: str
    command: str = ""


class PortInUseError(RuntimeError):
    pass


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

    ensure_runtime_ready()
    uvicorn.run(
        "app.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

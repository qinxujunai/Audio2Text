from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "web"
PYTHON_FILES = (
    "app/main.py",
    "app/pipeline.py",
    "app/runtime_preflight.py",
    "app/providers/transcription.py",
    "app/settings.py",
    "scripts/config.py",
    "scripts/doctor.py",
    "scripts/run_transcribe.py",
    "scripts/model_loader.py",
    "scripts/benchmark_transcription.py",
)


def _python_executable() -> str:
    if os.name == "nt":
        return str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    return str(PROJECT_ROOT / ".venv" / "bin" / "python")


def _resolve_command(name: str) -> str:
    candidates = [name]
    if os.name == "nt":
        candidates.extend([f"{name}.cmd", f"{name}.exe", f"{name}.bat"])
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return name


def _run(label: str, command: list[str], *, cwd: Path | None = None) -> int:
    print(f"\n== {label} ==", flush=True)
    completed = subprocess.run(
        command,
        cwd=str(cwd or PROJECT_ROOT),
        env=_quality_gate_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n", flush=True)
    if completed.returncode != 0:
        print(f"\nFAILED: {label} ({completed.returncode})", flush=True)
    return completed.returncode


def _quality_gate_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _force_utf8_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description="Run the non-Docker Audio2Text quality gate.")
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="Skip frontend typecheck/build while keeping doctor, compile, and backend tests.",
    )
    args = parser.parse_args()

    python = _python_executable()
    commands: list[tuple[str, list[str], Path | None]] = [
        ("doctor", [python, "-m", "scripts.doctor"], PROJECT_ROOT),
        ("py_compile", [python, "-m", "py_compile", *PYTHON_FILES], PROJECT_ROOT),
        ("backend tests", [python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], PROJECT_ROOT),
    ]
    if not args.backend_only:
        npm = _resolve_command("npm")
        commands.extend(
            [
                ("frontend type check", [npm, "exec", "tsc", "--", "--noEmit"], WEB_DIR),
                ("frontend build", [npm, "run", "build"], WEB_DIR),
            ]
        )

    for label, command, cwd in commands:
        return_code = _run(label, command, cwd=cwd)
        if return_code != 0:
            return return_code

    print("\nAudio2Text verify: ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

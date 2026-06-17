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
TRANSCRIBE_SMOKE_SNIPPET = r"""
import json
import sys
from pathlib import Path

from scripts.config import DEVICE, PROJECT_ROOT
from scripts.run_transcribe import transcribe_file

media_path = Path(sys.argv[1])
if not media_path.exists():
    raise FileNotFoundError(media_path)

output_dir = PROJECT_ROOT / "workspace" / "temp" / "verify-transcribe-smoke"
result = transcribe_file(media_path, output_folder=output_dir, output_stem=media_path.stem)
payload = result["result"]
if DEVICE == "cuda" and payload.get("device") != "cuda":
    raise RuntimeError(f"Expected CUDA runtime, got {payload.get('device')!r}")
if int(payload.get("segment_count") or 0) <= 0:
    raise RuntimeError("Transcription smoke produced no segments")
txt_file = Path(payload.get("txt_file") or "")
if not txt_file.exists():
    raise FileNotFoundError(f"Transcription smoke output text file not found: {txt_file}")
transcript_text = txt_file.read_text(encoding="utf-8", errors="ignore").strip()
if not transcript_text:
    raise RuntimeError(f"Transcription smoke output text is empty: {txt_file}")
print(json.dumps({
    "success": result["success"],
    "device": payload.get("device"),
    "compute_type": payload.get("compute_type"),
    "language": payload.get("language"),
    "segment_count": payload.get("segment_count"),
    "text_preview": transcript_text[:80],
    "output_dir": payload.get("output_dir"),
}, ensure_ascii=False))
"""


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
    parser.add_argument(
        "--transcribe-smoke-file",
        default="",
        help="Optional local audio/video file for a real Faster-Whisper transcription smoke test.",
    )
    args = parser.parse_args()

    python = _python_executable()
    commands: list[tuple[str, list[str], Path | None]] = [
        ("doctor", [python, "-m", "scripts.doctor"], PROJECT_ROOT),
        ("py_compile", [python, "-m", "py_compile", *PYTHON_FILES], PROJECT_ROOT),
        ("backend tests", [python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], PROJECT_ROOT),
    ]
    if args.transcribe_smoke_file.strip():
        commands.append(
            (
                "transcription smoke",
                [python, "-c", TRANSCRIBE_SMOKE_SNIPPET, args.transcribe_smoke_file.strip()],
                PROJECT_ROOT,
            )
        )
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

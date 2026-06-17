from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

from app.runtime_preflight import CUDA_RUNTIME_DLLS, _find_runtime_file, run_runtime_preflight
from app.settings import runtime_summary


REQUIRED_IMPORTS = (
    "fastapi",
    "uvicorn",
    "faster_whisper",
    "ctranslate2",
    "av",
    "httpx",
    "requests",
    "yt_dlp",
    "playwright",
    "opencc",
)


def _check_imports() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for module_name in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            rows.append({"name": module_name, "status": "missing", "detail": str(exc)})
        else:
            rows.append({"name": module_name, "status": "ok", "detail": ""})
    return rows


def _path_status(name: str, raw_path: str, *, expect_dir: bool = False) -> dict[str, str]:
    path = Path(raw_path)
    exists = path.is_dir() if expect_dir else path.exists()
    return {"name": name, "status": "ok" if exists else "missing", "detail": str(path)}


def _runtime_path_checks(summary: dict[str, Any]) -> list[dict[str, str]]:
    return [
        _path_status("workspace_dir", summary["workspace_dir"], expect_dir=True),
        _path_status("model_path", summary["model_path"], expect_dir=True),
        _path_status("ffmpeg_path", summary["ffmpeg_path"]),
        _path_status("playwright_browsers_dir", summary["playwright_browsers_dir"], expect_dir=True),
    ]


def _cuda_checks(summary: dict[str, Any]) -> list[dict[str, str]]:
    if summary.get("device") != "cuda":
        return []
    rows: list[dict[str, str]] = []
    for filename in CUDA_RUNTIME_DLLS:
        found = _find_runtime_file(filename)
        rows.append({"name": f"cuda:{filename}", "status": "ok" if found else "missing", "detail": str(found or "")})
    return rows


def _preflight_checks() -> tuple[bool, list[dict[str, str]], list[str], list[str]]:
    result = run_runtime_preflight()
    rows = [{"name": "runtime_preflight", "status": "ok" if result.ok else "failed", "detail": ""}]
    return result.ok, rows, result.fatal_errors, result.warnings


def run_doctor() -> dict[str, Any]:
    summary = runtime_summary()
    preflight_ok, preflight_rows, fatal_errors, warnings = _preflight_checks()
    checks = []
    checks.extend(_check_imports())
    checks.extend(_runtime_path_checks(summary))
    checks.extend(_cuda_checks(summary))
    checks.extend(preflight_rows)
    ok = preflight_ok and all(item["status"] == "ok" for item in checks)
    return {
        "ok": ok,
        "config": {
            "transcription_provider": summary["transcription_provider"],
            "transcription_available": summary["transcription_available"],
            "device": summary["device"],
            "compute_type": summary["compute_type"],
            "beam_size": summary["beam_size"],
            "vad_filter": summary["vad_filter"],
            "model_path_source": summary["model_path_source"],
            "ffmpeg_path_source": summary["ffmpeg_path_source"],
        },
        "checks": checks,
        "fatal_errors": fatal_errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Audio2Text runtime readiness without starting the API server.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    result = run_doctor()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result["checks"]:
            print(f"{item['status']:>7}  {item['name']:<28} {item['detail']}")
        if result["warnings"]:
            print("")
            print("Warnings:")
            for warning in result["warnings"]:
                print(f"- {warning}")
        if result["fatal_errors"]:
            print("")
            print("Fatal errors:")
            for error in result["fatal_errors"]:
                print(f"- {error}")
        print("")
        print("Audio2Text doctor:", "ok" if result["ok"] else "failed")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

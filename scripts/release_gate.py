from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "web"
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
FORBIDDEN_PUBLIC_UI_TOKENS = (
    "processing-mascot",
    "卡吱",
    "Windows 版",
    "Windows版",
)


def _python_executable() -> str:
    if os.name == "nt":
        return str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    return str(PROJECT_ROOT / ".venv" / "bin" / "python")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve_command(name: str) -> str:
    candidates = [name]
    if os.name == "nt":
        candidates.extend([f"{name}.cmd", f"{name}.exe", f"{name}.bat"])
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return name


def _run_step(
    label: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> None:
    print(f"\n==> {label}")
    print(" ".join(command))
    try:
        subprocess.run(command, cwd=str(cwd or PROJECT_ROOT), env=env, check=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{label} exceeded {timeout} seconds. Stop here and inspect the last printed Docker/build step instead of waiting indefinitely."
        ) from exc


def _ensure_ui_smoke_prerequisites() -> None:
    browsers_dir = PROJECT_ROOT / "workspace" / "runtime" / "playwright-browsers"
    if not browsers_dir.exists() or not any(item.name.startswith("chromium") for item in browsers_dir.iterdir()):
        raise RuntimeError(
            "UI smoke requires project Playwright Chromium under workspace/runtime/playwright-browsers."
        )


def _wait_for_json(url: str, *, timeout: float = 45.0) -> dict:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _run_docker_smoke(image_tag: str) -> None:
    print("\n==> Docker smoke")
    container_name = f"praxis-audio2text-gate-{uuid.uuid4().hex[:8]}"
    host_port = _free_port()
    workspace_mount = f"{(PROJECT_ROOT / 'workspace').resolve()}:/app/workspace"
    docker_bin = _resolve_command("docker")

    subprocess.run(
        [
            docker_bin,
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-p",
            f"{host_port}:8000",
            "-v",
            workspace_mount,
            image_tag,
        ],
        cwd=str(PROJECT_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )

    try:
        health_payload = _wait_for_json(f"http://127.0.0.1:{host_port}/health")
        if health_payload.get("status") != "ok":
            raise RuntimeError(f"/health returned unexpected payload: {health_payload}")

        config_payload = _wait_for_json(f"http://127.0.0.1:{host_port}/config")
        forbidden_keys = {"workspace_dir", "captures_dir", "artifacts_dir", "playwright_browsers_dir"}
        leaked = sorted(key for key in forbidden_keys if key in config_payload)
        if leaked:
            raise RuntimeError(f"/config leaked internal runtime keys: {', '.join(leaked)}")

        print(f"health ok on :{host_port}")
        print("config payload kept public fields only")
    finally:
        subprocess.run(
            [docker_bin, "stop", container_name],
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
        )


def _run_ui_smoke_isolated(*, env: dict[str, str]) -> None:
    test_names = [
        "tests.ui_smoke_suite.UISmokeTestCase.test_homepage_recent_history_is_stable",
        "tests.ui_smoke_suite.UISmokeTestCase.test_processing_uses_one_quiet_loading_indicator",
        "tests.ui_smoke_suite.UISmokeTestCase.test_recoverable_platform_failure_has_no_legacy_mascot",
        "tests.ui_smoke_suite.UISmokeTestCase.test_permanent_failure_has_one_quiet_exit_without_legacy_mascot",
        "tests.ui_smoke_suite.UISmokeTestCase.test_image_result_actions_render",
        "tests.ui_smoke_suite.UISmokeTestCase.test_mobile_result_actions_and_viewer_are_tappable",
        "tests.ui_smoke_suite.UISmokeTestCase.test_submit_flow_recovers_from_transient_capture_read",
        "tests.ui_smoke_suite.UISmokeTestCase.test_video_result_layout_stays_stable",
        "tests.ui_smoke_suite.UISmokeTestCase.test_result_workspace_uses_available_desktop_space",
    ]
    for test_name in test_names:
        _run_step(
            "UI smoke: " + test_name.rsplit(".", 1)[-1],
            [_python_executable(), "-m", "unittest", test_name],
            env=env,
        )


def _assert_public_ui_contract() -> None:
    """Reject the two visual regressions that previously escaped into builds."""
    if not FRONTEND_DIST_DIR.exists():
        raise RuntimeError(f"Frontend build output is missing: {FRONTEND_DIST_DIR}")

    files = [
        path
        for path in FRONTEND_DIST_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in {".html", ".js", ".css"}
    ]
    payload = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in files)
    leaked = [token for token in FORBIDDEN_PUBLIC_UI_TOKENS if token in payload]
    if leaked:
        raise RuntimeError(
            "Public UI contains retired product elements: " + ", ".join(leaked)
        )
    print("public UI contract: no mascot or Windows download entry")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Praxis AI local release gate: backend tests, frontend checks, UI smoke, Docker build, and optional live smoke.",
    )
    parser.add_argument(
        "--image-tag",
        default="praxis-audio2text",
        help="Docker image tag to build and smoke-test. Defaults to praxis-audio2text.",
    )
    parser.add_argument(
        "--with-live-smoke",
        action="store_true",
        help="Also run tests.test_platform_live_smoke against a running base URL.",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help="Treat this as the final pre-deploy check. Requires --with-live-smoke.",
    )
    parser.add_argument(
        "--live-smoke-base-url",
        default="",
        help="Base URL for live smoke. Falls back to AUDIO2TEXT_LIVE_SMOKE_BASE_URL.",
    )
    parser.add_argument(
        "--live-smoke-samples",
        default="",
        help="Sample file for live smoke. Falls back to AUDIO2TEXT_LIVE_SMOKE_SAMPLES.",
    )
    parser.add_argument(
        "--docker-build-timeout",
        type=int,
        default=300,
        help="Maximum seconds for Docker build before failing with a diagnostic message. Defaults to 300.",
    )
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Skip Docker build and container smoke. Use only when Docker Desktop is unavailable or the build is being diagnosed separately.",
    )
    parser.add_argument(
        "--deployment-contract-base-url",
        default=os.environ.get("AUDIO2TEXT_DEPLOYMENT_CONTRACT_BASE_URL", "").strip(),
        help="Verify deployed cache/build headers against this URL.",
    )
    parser.add_argument(
        "--extraction-benchmark-report",
        default=os.environ.get("AUDIO2TEXT_EXTRACTION_BENCHMARK_REPORT", "").strip(),
        help="Release extraction benchmark JSON; required by --final.",
    )
    parser.add_argument(
        "--transcription-benchmark-report",
        default=os.environ.get("AUDIO2TEXT_TRANSCRIPTION_BENCHMARK_REPORT", "").strip(),
        help="Release transcription benchmark JSON; required by --final.",
    )
    args = parser.parse_args()

    if args.final and not args.with_live_smoke:
        raise RuntimeError("Final release gate requires --with-live-smoke.")
    if args.final and not args.deployment_contract_base_url:
        raise RuntimeError("Final release gate requires --deployment-contract-base-url.")
    if args.final and not args.extraction_benchmark_report:
        raise RuntimeError("Final release gate requires --extraction-benchmark-report.")
    if args.final and not args.transcription_benchmark_report:
        raise RuntimeError("Final release gate requires --transcription-benchmark-report.")

    # Release tests deliberately exercise retry, challenge, and legacy-failure
    # fixtures.  Keep those synthetic events out of the live project's logs so
    # an operator can trust workspace/logs as runtime evidence after the gate.
    gate_logs_dir = PROJECT_ROOT / "workspace" / "temp" / "release-gate-logs"
    gate_logs_dir.mkdir(parents=True, exist_ok=True)
    gate_env = os.environ.copy()
    gate_env["AUDIO2TEXT_LOGS_DIR"] = str(gate_logs_dir)

    _run_step(
        "Backend tests",
        [_python_executable(), "-m", "unittest", "discover", "-s", "tests", '-p', "test_*.py"],
        env=gate_env,
    )
    npm_bin = _resolve_command("npm")
    docker_bin = _resolve_command("docker")
    _run_step("Frontend type check", [_resolve_command("node"), str(WEB_DIR / "node_modules/typescript/bin/tsc"), "--noEmit"], cwd=WEB_DIR)
    _run_step("Frontend build", [npm_bin, "run", "build"], cwd=WEB_DIR)
    _assert_public_ui_contract()
    _ensure_ui_smoke_prerequisites()
    _run_ui_smoke_isolated(env=gate_env)
    if args.extraction_benchmark_report and args.transcription_benchmark_report:
        _run_step(
            "Benchmark release evidence",
            [
                _python_executable(),
                "-m",
                "scripts.validate_benchmark_reports",
                "--extraction",
                args.extraction_benchmark_report,
                "--transcription",
                args.transcription_benchmark_report,
            ],
            env=gate_env,
        )
    if args.deployment_contract_base_url:
        _run_step(
            "Deployment cache/build contract",
            [
                _python_executable(),
                "-m",
                "scripts.verify_deployment_contract",
                args.deployment_contract_base_url,
            ],
            env=gate_env,
        )
    if not args.skip_docker:
        _run_step(
            "Docker build",
            [docker_bin, "build", "--progress=plain", "-t", args.image_tag, "."],
            cwd=PROJECT_ROOT,
            timeout=args.docker_build_timeout,
        )
        _run_docker_smoke(args.image_tag)

    if args.with_live_smoke:
        live_smoke_base_url = args.live_smoke_base_url.strip() or os.environ.get("AUDIO2TEXT_LIVE_SMOKE_BASE_URL", "").strip()
        if not live_smoke_base_url:
            raise RuntimeError("Live smoke requested, but no base URL was provided.")

        live_smoke_env = os.environ.copy()
        live_smoke_env["AUDIO2TEXT_LIVE_SMOKE_BASE_URL"] = live_smoke_base_url
        if args.live_smoke_samples.strip():
            live_smoke_env["AUDIO2TEXT_LIVE_SMOKE_SAMPLES"] = args.live_smoke_samples.strip()

        _run_step(
            "Platform live smoke",
            [_python_executable(), "-m", "unittest", "tests.test_platform_live_smoke"],
            env={**live_smoke_env, **gate_env},
        )

    print("\nRelease gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

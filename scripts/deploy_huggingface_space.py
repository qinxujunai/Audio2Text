from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

from scripts.config import PROJECT_ROOT


DEFAULT_SPACE_NAME = "wanxiang-chengwen-preview"
DEFAULT_SPACE_VARIABLES = {
    "AUDIO2TEXT_API_HOST": "0.0.0.0",
    "AUDIO2TEXT_API_PORT": "8000",
    "AUDIO2TEXT_DEPLOYMENT_MODE": "cloud_preview",
    "AUDIO2TEXT_PUBLIC_PREVIEW_MODE": "1",
    "AUDIO2TEXT_ALLOW_DEGRADED_START": "1",
    "AUDIO2TEXT_TRANSCRIPTION_PROVIDER": "openai_compatible",
}

SPACE_SOURCE_PATHS = [
    "app",
    "web",
    "scripts/__init__.py",
    "scripts/config.py",
    "scripts/logger.py",
    "scripts/model_loader.py",
    "scripts/run_transcribe.py",
    "scripts/start_api.py",
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    "Dockerfile",
    "requirements.txt",
    "audio2text.settings.example.json",
]

SPACE_README = """---
title: Wanxiang Chengwen
sdk: docker
app_port: 8000
---

# 万象成文

单页预览版：公开链接或本地文件输入，同页处理，同页交付正文、图片和视频素材。

当前 Space 是云端预览环境：

- 图文链接、网页正文和带字幕的视频可先体验。
- 音视频转写需要配置 OpenAI-compatible 转写服务。
- 免费 Space 的运行数据不作为长期持久存储。
"""

REMOTE_DELETE_PATTERNS = [
    "Assets/Models/*",
    "workspace/*",
    "tests_runtime/*",
    "snapshots/*",
    "**/__pycache__/*",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.pyd",
    "docs/*",
    "tests/*",
    "web/node_modules/*",
    "frontend/dist/*",
    "audio2text.settings.json",
    "tests/live_smoke_samples.local.json",
    "run_transcribe.bat",
    "start_api.bat",
    "scripts/public_preview.py",
    "scripts/start_cloudflare_tunnel.py",
    "scripts/start_localtunnel.py",
]


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("[CMD] " + " ".join(command))
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed


def _login_message() -> str:
    if sys.platform.startswith("win"):
        return (
            "Hugging Face is not logged in. Run this first:\n"
            "  .\\.venv\\Scripts\\hf.exe auth login\n"
            "If you do not have a token yet, create one at:\n"
            "  https://huggingface.co/settings/tokens"
        )
    return (
        "Hugging Face is not logged in. Run this first:\n"
        "  ./.venv/bin/hf auth login\n"
        "If you do not have a token yet, create one at:\n"
        "  https://huggingface.co/settings/tokens"
    )


def _require_hf() -> str:
    candidates = ["hf"]
    if sys.platform.startswith("win"):
        candidates.insert(0, str(PROJECT_ROOT / ".venv" / "Scripts" / "hf.exe"))
    else:
        candidates.insert(0, str(PROJECT_ROOT / ".venv" / "bin" / "hf"))

    for candidate in candidates:
        hf = shutil.which(candidate) if candidate == "hf" else candidate
        if hf and Path(hf).exists():
            return hf

    raise SystemExit(
        "hf CLI is not installed in the project .venv or system PATH. "
        "Install it with: .\\.venv\\Scripts\\python.exe -m pip install -U huggingface_hub[hf_xet]\n"
        "Then login with: .\\.venv\\Scripts\\hf.exe auth login"
    )


def _current_hf_username() -> str:
    try:
        payload = HfApi().whoami()
    except Exception as exc:
        raise SystemExit(_login_message()) from exc

    username = str(payload.get("name") or payload.get("fullname") or "").strip()
    if not username:
        raise SystemExit(f"Could not determine the Hugging Face username from: {json.dumps(payload, ensure_ascii=False)}")
    return username


def _resolve_space_id(space_id: str | None) -> str:
    if space_id and space_id.strip():
        return space_id.strip()
    return f"{_current_hf_username()}/{DEFAULT_SPACE_NAME}"


def _set_default_space_variables(space_id: str) -> None:
    api = HfApi()
    for key, value in DEFAULT_SPACE_VARIABLES.items():
        print(f"[VAR] {key}={value}")
        api.add_space_variable(repo_id=space_id, key=key, value=value)


def _copy_tree_clean(src: Path, dst: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
        return {name for name in names if name in ignored or name.endswith((".pyc", ".pyo", ".pyd"))}

    shutil.copytree(src, dst, ignore=ignore)


def _prepare_space_bundle() -> tempfile.TemporaryDirectory[str]:
    tempdir = tempfile.TemporaryDirectory(prefix="audio2text-hf-space-")
    bundle_root = Path(tempdir.name)
    for relative in SPACE_SOURCE_PATHS:
        source = PROJECT_ROOT / relative
        target = bundle_root / relative
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            _copy_tree_clean(source, target)
        else:
            shutil.copy2(source, target)

    (bundle_root / "README.md").write_text(SPACE_README, encoding="utf-8")
    return tempdir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create/update a Hugging Face Docker Space for Audio2Text."
    )
    parser.add_argument(
        "space_id",
        nargs="?",
        help="Hugging Face Space id, for example: your-username/praxis-audio2text",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create the Space as private. Omit for a public preview link.",
    )
    parser.add_argument(
        "--skip-default-variables",
        action="store_true",
        help="Do not set default cloud preview variables on the Space.",
    )
    args = parser.parse_args()

    hf = _require_hf()
    _run([hf, "auth", "whoami"])
    space_id = _resolve_space_id(args.space_id)

    create_command = [
        hf,
        "repos",
        "create",
        space_id,
        "--type",
        "space",
        "--space-sdk",
        "docker",
        "--exist-ok",
    ]
    if args.private:
        create_command.append("--private")
    _run(create_command)
    if not args.skip_default_variables:
        _set_default_space_variables(space_id)

    upload_command = ["python-api", "upload_folder", space_id]
    print("[CMD] " + " ".join(upload_command))
    with _prepare_space_bundle() as bundle_root:
        HfApi().upload_folder(
            repo_id=space_id,
            folder_path=bundle_root,
            repo_type="space",
            commit_message="Deploy Audio2Text Docker Space",
            delete_patterns=REMOTE_DELETE_PATTERNS,
        )

    print("\n[INFO] Upload complete.")
    print(f"[INFO] Space page: https://huggingface.co/spaces/{space_id}")
    print(f"[INFO] App URL: https://{space_id.replace('/', '-')}.hf.space")
    print("\n[IMPORTANT]")
    print("Cloud preview mode auto-configures free Cloudflare Workers AI Whisper for transcription.")
    print("No API keys needed for audio/video transcription.")
    print("\nThese public cloud-preview variables were set automatically unless --skip-default-variables was used:")
    for key, value in DEFAULT_SPACE_VARIABLES.items():
        print(f"  {key}={value}")
    return 0


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        print("[WARN] Local helper was tested with Python 3.11+.")
    raise SystemExit(main())

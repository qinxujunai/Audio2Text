from __future__ import annotations

import argparse
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from scripts.config import PROJECT_ROOT, RUNTIME_DIR, TEMP_DIR, WORKSPACE_DIR


@dataclass(frozen=True)
class CleanupTarget:
    label: str
    path: Path
    kind: str = "directory"


DEFAULT_TARGETS: tuple[CleanupTarget, ...] = (
    CleanupTarget("tests runtime artifacts", PROJECT_ROOT / "tests_runtime"),
    CleanupTarget("workspace temp files", TEMP_DIR),
    CleanupTarget("workspace debug artifacts", WORKSPACE_DIR / "debug_douyin"),
    CleanupTarget("workspace scratch sandbox", WORKSPACE_DIR / "test-sandbox"),
    CleanupTarget("workspace local transcribe output", WORKSPACE_DIR / "output"),
    CleanupTarget("legacy runtime stderr log", RUNTIME_DIR / "api.err.log", kind="file"),
    CleanupTarget("legacy runtime stdout log", RUNTIME_DIR / "api.out.log", kind="file"),
)


def _runtime_smoke_targets() -> list[CleanupTarget]:
    return [
        CleanupTarget(f"legacy runtime smoke result {path.name}", path, kind="file")
        for path in sorted(RUNTIME_DIR.glob("smoke_results*.json"))
    ]


def _describe_directory(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0

    file_count = 0
    total_size = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            file_count += 1
            try:
                total_size += entry.stat().st_size
            except OSError:
                continue
    return file_count, total_size


def _describe_target(target: CleanupTarget) -> tuple[int, int]:
    if target.kind == "file":
        if not target.path.exists():
            return 0, 0
        try:
            return 1, target.path.stat().st_size
        except OSError:
            return 1, 0
    return _describe_directory(target.path)


def _clear_directory(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        return

    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, onerror=_handle_remove_readonly)
        else:
            child.unlink(missing_ok=True)

    path.mkdir(parents=True, exist_ok=True)


def _handle_remove_readonly(function, target, exc_info) -> None:
    try:
        os.chmod(target, stat.S_IWRITE)
    except OSError:
        raise exc_info[1]
    function(target)


def _clear_target(target: CleanupTarget) -> None:
    if target.kind == "file":
        target.path.unlink(missing_ok=True)
        return
    _clear_directory(target.path)


def _format_size(size: int) -> str:
    return f"{size / (1024 * 1024):.2f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean transient runtime and test artifacts without touching captures, artifacts, or runtime dependencies.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete the detected transient artifacts. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()

    targets = [*DEFAULT_TARGETS, *_runtime_smoke_targets()]

    print("Praxis runtime cleanup")
    print("======================")

    total_files = 0
    total_size = 0
    found = False

    for target in targets:
        file_count, size = _describe_target(target)
        if file_count == 0 and size == 0 and not target.path.exists():
            continue

        found = True
        total_files += file_count
        total_size += size
        print(f"- {target.label}: {target.path}")
        print(f"  files: {file_count}, size: {_format_size(size)}")
        if args.yes:
            _clear_target(target)
            print("  action: cleared")
        else:
            print("  action: dry run")

    if not found:
        print("No transient runtime artifacts found.")
        return 0

    print("----------------------")
    print(f"total files: {total_files}")
    print(f"total size: {_format_size(total_size)}")

    if not args.yes:
        print("Run again with --yes to remove these artifacts.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

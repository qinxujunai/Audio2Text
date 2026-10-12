"""Fingerprint the repository snapshot without including ignored runtime data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


def source_fingerprint(root: Path) -> dict:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    )
    files = sorted({name.decode("utf-8") for name in output.split(b"\0") if name})
    hashes = {}
    for relative in files:
        if relative.startswith(".serena/"):
            continue
        path = root / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"schema_version": 1, "source_fingerprint": hashlib.sha256(canonical).hexdigest(), "files": hashes}


def resource_fingerprint(root: Path) -> dict:
    """Bind the full packaged runtime; exclude only its self-referential manifest."""
    root = root.absolute()
    for parent in (root, *root.parents):
        if getattr(parent.lstat(), "st_file_attributes", 0) & 0x400 or parent.is_symlink():
            raise ValueError("Packaged resource path must not traverse links")
    hashes = {}
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            if path.is_symlink():
                raise ValueError("Packaged resources must not contain links")
            # Python 3.11 exposes Windows junctions through the reparse attribute.
            if getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Packaged resources must not contain reparse points")
            if path.is_dir():
                pending.append(path)
            elif path.is_file() and path.relative_to(root).as_posix() != "sidecar-build-manifest.json":
                with path.open("rb") as source:
                    hashes[path.relative_to(root).as_posix()] = hashlib.file_digest(source, "sha256").hexdigest()
    hashes = dict(sorted(hashes.items()))
    canonical = json.dumps(hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"resource_tree_sha256": hashlib.sha256(canonical).hexdigest(), "resource_files": hashes}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path)
    args = parser.parse_args()
    print(json.dumps(resource_fingerprint(args.resources) if args.resources else source_fingerprint(Path(__file__).resolve().parents[1]), ensure_ascii=True))

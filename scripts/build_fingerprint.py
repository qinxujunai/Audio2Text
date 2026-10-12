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


if __name__ == "__main__":
    print(json.dumps(source_fingerprint(Path(__file__).resolve().parents[1]), ensure_ascii=True))

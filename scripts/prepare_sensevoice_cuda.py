"""Download pinned, hash-verified GPU components into project-owned runtime folders."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
import zipfile
import time
import requests

ROOT = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
FILES = [
    ("csukuangfj2/sherpa-onnx-wheels", "4de10716b1043c13380c79f3f12127a6c6429650",
     "cuda/1.13.4/sherpa_onnx-1.13.4+cuda12.cudnn9-cp311-cp311-win_amd64.whl",
     "511ff17d1c6bb9f1d573078ab8b42b3c9873fc7464e598ea6a61d281391f657b", 200148105,
     ROOT / "candidates" / "sensevoice-cuda" / "sherpa-cuda.whl"),
    ("csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17", "2365baeacb507f821a0c8120fcee3d484dba7a07",
     "model.onnx", "977016bd9c79f9eb343430b5cc305e07ab64d5212dff41b0dcfa1694bee9a8cb", 937617178,
     ROOT / "models" / "sensevoice" / "small-fp32" / "model.onnx"),
]

def download(item):
    repo, revision, filename, expected, size, target = item
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == size and hashlib.file_digest(target.open("rb"), "sha256").hexdigest() == expected:
        return target
    if target.exists():
        raise RuntimeError(f"Existing file does not match pinned component: {target.name}")
    partial = target.with_suffix(target.suffix + ".part")
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}?download=true"
    # Bounded range requests resume interruptions without restarting a 900 MB model.
    pieces = target.parent / (target.name + ".pieces")
    pieces.mkdir(exist_ok=True)
    step = 4 * 2 ** 20
    if partial.exists():
        with partial.open("rb") as handle:
            for index in range(partial.stat().st_size // step):
                (pieces / str(index)).write_bytes(handle.read(step))
    def fetch(index):
        start, end = index * step, min(size, (index + 1) * step) - 1
        part = pieces / str(index)
        if part.exists() and part.stat().st_size == end - start + 1:
            return
        for attempt in range(4):
            try:
                response = requests.get(url + "&fresh=" + str(time.time_ns()),
                    headers={"Range": f"bytes={start}-{end}"}, timeout=(20, 45))
                response.raise_for_status()
                if response.status_code != 206 or len(response.content) != end - start + 1:
                    raise RuntimeError("Server did not honor a bounded range request")
                part.write_bytes(response.content)
                return
            except (requests.RequestException, RuntimeError):
                if attempt == 3:
                    raise
                time.sleep(1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        completed = 0
        total = (size + step - 1) // step
        for _ in pool.map(fetch, range(total)):
            completed += 1
            if completed % 16 == 0:
                print(f"{target.name}: {round(100*completed/total)}%", flush=True)
    with partial.open("wb") as handle:
        for index in range(total):
            handle.write((pieces / str(index)).read_bytes())
    with partial.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if partial.stat().st_size != size or actual != expected:
        raise RuntimeError(f"Component integrity check failed: {target.name}")
    partial.replace(target)
    print(f"Verified {target.name}: {size} bytes", flush=True)
    return target

def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(download, FILES))
    wheel, model = paths
    site = wheel.parent / "site"
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.infolist():
            destination = (site / entry.filename).resolve()
            if not destination.is_relative_to(site.resolve()):
                raise RuntimeError("Unsafe archive entry")
        archive.extractall(site)
    tokens = model.parent / "tokens.txt"
    if not tokens.exists():
        response = requests.get("https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365baeacb507f821a0c8120fcee3d484dba7a07/tokens.txt", timeout=30)
        response.raise_for_status()
        content = response.content
        # Git blob hash from the model publisher's pinned tree.
        git_blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if git_blob != "2cfc92fc2ff26aaa690b7c01fd96b41109413881":
            raise RuntimeError("Token integrity check failed")
        tokens.write_bytes(content)
    (wheel.parent / "provenance.json").write_text(json.dumps({"components": [
        {"repository": x[0], "revision": x[1], "file": x[2], "sha256": x[3], "size_bytes": x[4]}
        for x in FILES]}, indent=2), encoding="utf-8")
    print("GPU components prepared without changing the project venv.", flush=True)

if __name__ == "__main__":
    main()

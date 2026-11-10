"""Build the SenseVoice runtime pack reproducibly and record it in runtime-packs.json.

The pack holds the general-purpose SenseVoice Small int8 model (sherpa-onnx export
2024-07-17), its tokens and the Silero VAD model used to cut audio at pauses.
Every input is checked against a pinned SHA-256, and the zip is written with fixed
timestamps and a fixed file order, so building twice gives the same bytes.

Usage (from the repository root):
    python -m scripts.build_runtime_pack --sensevoice-dir <extracted 2024-07-17 folder> \
        --vad <silero_vad.onnx> --out <folder> [--write-manifest]

Sources:
    https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2
    https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

PACK_ID = "sensevoice-small-int8-general"
PACK_VERSION = "2024.07.17"
ARCHIVE_NAME = f"{PACK_ID}-{PACK_VERSION}.zip"
INSTALL_SUBDIR = "models/sensevoice/general-int8"
FIXED_TIME = (2024, 7, 17, 0, 0, 0)

PINNED_INPUTS = {
    "model.int8.onnx": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
    "tokens.txt": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
    "silero_vad.onnx": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
}

MODEL_SOURCE = """Model: sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17 (int8)
Original weights: FunAudioLLM/SenseVoiceSmall, general-purpose (Mandarin, English, Japanese, Korean, Cantonese)
Converted release: https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
Conversion and usage: https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
Model license: https://github.com/FunAudioLLM/SenseVoice (see THIRD_PARTY_NOTICES.md)

Voice activity detection: silero_vad.onnx from the same sherpa-onnx release page
Silero VAD source and license (MIT): https://github.com/snakers4/silero-vad
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_inputs(sensevoice_dir: Path, vad: Path) -> dict[str, Path]:
    files = {
        "model.int8.onnx": sensevoice_dir / "model.int8.onnx",
        "tokens.txt": sensevoice_dir / "tokens.txt",
        "silero_vad.onnx": vad,
    }
    for name, path in files.items():
        if not path.is_file():
            raise SystemExit(f"missing input: {path}")
        actual = sha256(path)
        if actual != PINNED_INPUTS[name]:
            raise SystemExit(f"{name}: SHA-256 {actual} does not match the pinned {PINNED_INPUTS[name]}")
    return files


def build(files: dict[str, Path], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / ARCHIVE_NAME
    entries = [(name, path.read_bytes()) for name, path in files.items()]
    entries.append(("MODEL_SOURCE.txt", MODEL_SOURCE.encode("utf-8")))
    with zipfile.ZipFile(archive, "w") as handle:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 0
            handle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return archive


def write_manifest(manifest_path: Path, archive: Path) -> dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack = next(item for item in payload["packs"] if item["id"] == PACK_ID)
    pack["sha256"] = sha256(archive)
    pack["size_bytes"] = archive.stat().st_size
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sensevoice-dir", type=Path, required=True)
    parser.add_argument("--vad", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("runtime-packs.json"))
    parser.add_argument("--write-manifest", action="store_true", help="record size and SHA-256 in runtime-packs.json")
    args = parser.parse_args()
    archive = build(collect_inputs(args.sensevoice_dir, args.vad), args.out)
    print(json.dumps({"archive": str(archive), "size_bytes": archive.stat().st_size, "sha256": sha256(archive)}, indent=2))
    if args.write_manifest:
        print(json.dumps(write_manifest(args.manifest, archive), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Compare engines on an existing local media clip; never alter its source."""
import argparse
import json
import time
from pathlib import Path

from scripts.config import FFMPEG_PATH
from app.runtime_preflight import configure_cuda_runtime_search_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("media")
    parser.add_argument("--output", required=True)
    parser.add_argument("--sensevoice-model", required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    import subprocess
    clip = root / "sample-180s.wav"
    subprocess.run([str(FFMPEG_PATH), "-hide_banner", "-loglevel", "error", "-y", "-ss", "60", "-i", args.media,
                    "-t", "180", "-vn", "-ar", "16000", "-ac", "1", str(clip)], check=True)
    from app.providers.transcription import LocalSenseVoiceProvider
    from scripts.run_transcribe import transcribe_file
    from scripts.model_loader import get_model_runtime
    configure_cuda_runtime_search_path()
    start = time.perf_counter()
    result = transcribe_file(str(clip), output_folder=root / "cuda", output_stem="sample")
    gpu_seconds = time.perf_counter() - start
    start = time.perf_counter()
    cpu = LocalSenseVoiceProvider(args.sensevoice_model).transcribe(str(clip), capture_id="benchmark", output_folder=root)
    cpu_seconds = time.perf_counter() - start
    runtime = get_model_runtime()
    if runtime["device"] != "cuda":
        raise RuntimeError("CUDA benchmark did not run on GPU")
    report = {"clip_seconds": 180, "cuda": {**runtime, "seconds": round(gpu_seconds, 3), "segment_count": result["result"]["segment_count"]},
              "cpu_sensevoice": {"seconds": round(cpu_seconds, 3), "segment_count": cpu.segment_count},
              "speed_ratio": round(cpu_seconds / gpu_seconds, 3), "same_model": False,
              "note": "Warm/cold startup and model differences apply. No accuracy score established."}
    (root / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()

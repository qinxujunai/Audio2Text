"""Same FP32 SenseVoice weights/device comparison, plus real long-file CUDA run."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import statistics
import time

from app.sensevoice_cuda import SenseVoiceCudaWorker
from app.providers.transcription import LocalSenseVoiceProvider, LocalSenseVoiceCudaProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--long-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    model = root / "workspace/runtime/models/sensevoice/small-fp32"
    sdk = root / "workspace/runtime/candidates/sensevoice-cuda/site"
    parts = list(LocalSenseVoiceProvider._iter_audio_chunks(args.sample))
    duration = sum(len(p) for p in parts) / 16000
    report = {"model": "SenseVoice Small", "weights": "FP32 ONNX, identical model.onnx for all rows",
              "sample": str(args.sample), "duration_seconds": duration,
              "accuracy": "No human reference for this user clip; no accuracy percentage inferred.", "runs": []}
    for device, batch in (("cpu", 1), ("cuda", 1), ("cuda", 4)):
        worker = SenseVoiceCudaWorker(model, sdk, device=device)
        try:
            runtime = worker.warm()
            times = []
            for run in range(3):
                started = time.perf_counter()
                output = []
                for start in range(0, len(parts), batch):
                    output.extend(worker.decode(parts[start:start+batch]))
                seconds = time.perf_counter() - started
                times.append(seconds)
                print(f"{device} batch={batch} run={run+1}: {seconds:.3f}s", flush=True)
            row = {"device": device, "batch_size": batch, "runtime": runtime, "seconds": times,
                   "median_seconds": statistics.median(times), "realtime_factor": statistics.median(times)/duration,
                   "texts": output}
            report["runs"].append(row)
        finally:
            worker.close()
    report["same_batch_cuda_speedup"] = report["runs"][0]["median_seconds"] / report["runs"][1]["median_seconds"]
    report["cpu_batch1_to_cuda_batch4_speedup"] = report["runs"][0]["median_seconds"] / report["runs"][2]["median_seconds"]
    report["cpu_cuda_batch1_texts_equal"] = report["runs"][0]["texts"] == report["runs"][1]["texts"]
    if args.long_file:
        provider = LocalSenseVoiceCudaProvider(model, sdk)
        provider.worker.warm()
        started = time.perf_counter()
        previous = [0]
        def progress(ratio, detail):
            if ratio - previous[0] >= .1 or ratio == 1:
                previous[0] = ratio
                print(f"Long file: {ratio:.0%}", flush=True)
        try:
            result = provider.transcribe(str(args.long_file), capture_id="benchmark", output_folder=args.output.parent, progress_callback=progress)
            report["long_file"] = {"file": str(args.long_file), "elapsed_seconds": time.perf_counter()-started,
                                   "chars": len(result.transcript_text), "segments": result.segment_count,
                                   "final_timeline": result.timeline_text.splitlines()[-1].split(" ", 3)[:3]}
            args.output.with_suffix(".long-transcript.txt").write_text(result.transcript_text, encoding="utf-8")
        finally:
            provider.worker.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {args.output}", flush=True)


if __name__ == "__main__":
    main()

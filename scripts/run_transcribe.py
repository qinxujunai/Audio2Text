from pathlib import Path

import argparse
import json
from typing import Callable

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - optional dependency at runtime
    OpenCC = None

from scripts.config import (
    BEAM_SIZE,
    EXPORT_MARKDOWN,
    EXPORT_META,
    EXPORT_PLAIN_TEXT,
    EXPORT_TIMESTAMP_TEXT,
    LANGUAGE,
    OUTPUT_DIR,
    VAD_FILTER,
)
from scripts.model_loader import get_model, get_model_runtime
from scripts.logger import get_logger

logger = get_logger("transcribe", "transcribe.log")
_opencc = OpenCC("t2s") if OpenCC else None


def _normalize_requested_language(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "auto", "detect", "none"}:
        return None
    return normalized


def _should_convert_to_simplified(language: str | None) -> bool:
    normalized = str(language or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith(("zh", "cmn", "yue", "wuu"))


def _normalize_segment_text(value: str, *, convert_to_simplified: bool) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _opencc is None or not convert_to_simplified:
        return text
    return _opencc.convert(text)


def transcribe_file(
    file_path: str,
    url: str = None,
    output_folder: str | Path | None = None,
    output_stem: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
):
    file_path = Path(file_path)
    logger.info(f"开始转写: {file_path}")

    if not file_path.exists():
        logger.error(f"上传文件不存在: {file_path}")
        raise FileNotFoundError(f"上传文件不存在: {file_path}")

    output_folder = Path(output_folder) if output_folder else (OUTPUT_DIR / file_path.stem)
    output_folder.mkdir(parents=True, exist_ok=True)
    output_stem = output_stem or file_path.stem

    model = get_model()
    if progress_callback is not None:
        progress_callback(0.02, "正在加载本地转写模型。")

    requested_language = _normalize_requested_language(LANGUAGE)
    segments, info = model.transcribe(
        str(file_path),
        language=requested_language,
        task="transcribe",
        beam_size=BEAM_SIZE,
        vad_filter=VAD_FILTER,
    )
    if progress_callback is not None:
        progress_callback(0.08, "本地转写已开始，正在逐段识别。")
    duration_seconds = float(getattr(info, "duration", 0.0) or 0.0)
    segments = list(segments)
    detected_language = str(getattr(info, "language", "") or "").strip().lower()
    convert_to_simplified = _should_convert_to_simplified(detected_language or requested_language)
    plain_segments: list[str] = []
    for segment in segments:
        plain_segments.append(_normalize_segment_text(segment.text, convert_to_simplified=convert_to_simplified))
        if progress_callback is None:
            continue
        segment_end = float(getattr(segment, "end", 0.0) or 0.0)
        if duration_seconds > 0:
            ratio = min(max(segment_end / duration_seconds, 0.08), 1.0)
            progress_callback(ratio, f"正在转写音频片段（{segment_end:.1f}s / {duration_seconds:.1f}s）。")

    if progress_callback is not None:
        progress_callback(1.0, "本地转写已完成，正在写入结果文件。")

    runtime = get_model_runtime()

    txt_file = output_folder / f"{output_stem}.txt"
    timestamp_file = output_folder / f"{output_stem}_timestamp.txt"
    md_file = output_folder / f"{output_stem}.md"
    meta_file = output_folder / f"{output_stem}_meta.txt"

    if EXPORT_PLAIN_TEXT:
        with open(txt_file, "w", encoding="utf-8") as f_txt:
            for text in plain_segments:
                f_txt.write(text + "\n")

    if EXPORT_TIMESTAMP_TEXT:
        with open(timestamp_file, "w", encoding="utf-8") as f_timestamp:
            for segment, text in zip(segments, plain_segments):
                f_timestamp.write(
                    f"[{segment.start:.2f}s - {segment.end:.2f}s] {text}\n"
                )

    if EXPORT_MARKDOWN:
        with open(md_file, "w", encoding="utf-8") as f_md:
            f_md.write(f"# {output_stem}\n\n")
            for segment, text in zip(segments, plain_segments):
                f_md.write(
                    f"- **{segment.start:.2f}s -> {segment.end:.2f}s** {text}\n"
                )

    if EXPORT_META:
        with open(meta_file, "w", encoding="utf-8") as f_meta:
            f_meta.write(f"input_file={file_path.name}\n")
            f_meta.write(f"language={info.language}\n")
            f_meta.write(f"language_probability={info.language_probability}\n")
            f_meta.write(f"device={runtime['device']}\n")
            f_meta.write(f"compute_type={runtime['compute_type']}\n")
            f_meta.write(f"model_path={runtime['model_path']}\n")
            f_meta.write(f"segment_count={len(segments)}\n")

    logger.info(f"转写完成: {file_path}")

    return {
        "success": True,
        "message": "文件上传并转写成功",
        "result": {
            "input_file_name": file_path.name,
            "output_dir": str(output_folder),
            "result_folder_name": output_folder.name,
            "txt_file": str(txt_file) if EXPORT_PLAIN_TEXT else None,
            "timestamp_file": str(timestamp_file) if EXPORT_TIMESTAMP_TEXT else None,
            "markdown_file": str(md_file) if EXPORT_MARKDOWN else None,
            "meta_file": str(meta_file) if EXPORT_META else None,
            "language": info.language,
            "language_probability": info.language_probability,
            "device": runtime["device"],
            "compute_type": runtime["compute_type"],
            "model_path": runtime["model_path"],
            "segment_count": len(segments),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transcribe a local audio or video file into workspace/output."
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to the local media file.",
    )
    args = parser.parse_args()

    if not args.file:
        parser.print_help()
        return 1

    result = transcribe_file(args.file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

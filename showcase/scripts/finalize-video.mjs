import { execFile } from "node:child_process";
import { unlink } from "node:fs/promises";
import { resolve } from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const docs = resolve(import.meta.dirname, "..", "..", "docs", "assets");
const input = resolve(docs, "wanxiang-demo.raw.mp4");
const output = resolve(docs, "wanxiang-demo.mp4");
await run("ffmpeg", [
  "-loglevel", "error", "-y", "-i", input,
  "-an", "-c:v", "libx264", "-crf", "20", "-preset", "medium",
  "-pix_fmt", "yuv420p", "-color_range", "tv", "-movflags", "+faststart", output,
]);
await unlink(input);

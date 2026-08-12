import { copyFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..", "..");
const docs = resolve(root, "docs", "assets");
const site = resolve(root, "site", "assets");
await mkdir(site, { recursive: true });
await Promise.all([
  copyFile(resolve(docs, "wanxiang-demo.mp4"), resolve(site, "wanxiang-demo.mp4")),
  copyFile(resolve(docs, "wanxiang-demo-poster.jpg"), resolve(site, "demo-poster.jpg")),
  copyFile(resolve(docs, "wanxiang-social.png"), resolve(site, "social-preview.png")),
]);

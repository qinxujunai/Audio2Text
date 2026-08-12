import { execFile } from "node:child_process";
import { copyFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const root = resolve(import.meta.dirname, "..");
const web = resolve(root, "web");
const source = resolve(web, "public", "brand-mark.svg");
const siteAssets = resolve(root, "site", "assets");
const docsAssets = resolve(root, "docs", "assets");

const tauri = resolve(web, "node_modules", "@tauri-apps", "cli", "tauri.js");
await run(process.execPath, [tauri, "icon", source], { cwd: web });
await mkdir(siteAssets, { recursive: true });
await mkdir(docsAssets, { recursive: true });
await Promise.all([
  copyFile(source, resolve(siteAssets, "brand-mark.svg")),
  copyFile(source, resolve(root, "showcase", "public", "brand-mark.svg")),
  copyFile(resolve(web, "src-tauri", "icons", "32x32.png"), resolve(siteAssets, "favicon.png")),
  copyFile(resolve(web, "src-tauri", "icons", "icon.png"), resolve(docsAssets, "wanxiang-icon-1024.png")),
]);

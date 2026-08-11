import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { Config } from "@remotion/cli/config";

const projectChromium = resolve(
  process.cwd(),
  "..",
  "workspace",
  "runtime",
  "playwright-browsers",
  "chromium-1228",
  "chrome-win64",
  "chrome.exe",
);

if (existsSync(projectChromium)) {
  Config.setBrowserExecutable(projectChromium);
}

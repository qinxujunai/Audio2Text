import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { writeFileSync } from "node:fs";

import { createFrontendManifest } from "./scripts/frontend_fingerprint.mjs";

function frontendBuildManifest() {
  return {
    name: "frontend-build-manifest",
    writeBundle() {
      const outputDirectory = resolve(__dirname, "../frontend/dist");
      const manifest = createFrontendManifest(outputDirectory);
      writeFileSync(
        resolve(outputDirectory, "build-manifest.json"),
        `${JSON.stringify(manifest, null, 2)}\n`,
        "utf8",
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), frontendBuildManifest()],
  server: {
    proxy: {
      "/config": "http://127.0.0.1:8000",
      "/v1": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: resolve(__dirname, "../frontend/dist"),
    emptyOutDir: true,
  },
});

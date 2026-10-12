import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const projectRoot = resolve(root, "..");

function collectFiles(directory, files = []) {
  if (!existsSync(directory)) return files;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) collectFiles(path, files);
    else if (entry.isFile()) files.push(path);
  }
  return files;
}

function trackedInputFiles() {
  const paths = [
    join(root, "index.html"),
    join(root, "package.json"),
    join(root, "package-lock.json"),
    join(root, "vite.config.ts"),
    ...collectFiles(join(root, "scripts")),
    ...collectFiles(join(root, "src")),
    ...collectFiles(join(root, "public")),
  ];
  return paths.filter((path) => existsSync(path)).sort();
}

function hashFiles(paths) {
  const hash = createHash("sha256");
  for (const path of paths) {
    hash.update(relative(projectRoot, path).replaceAll("\\", "/"));
    hash.update("\0");
    hash.update(readFileSync(path));
    hash.update("\0");
  }
  return hash.digest("hex");
}

function gitSha() {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: projectRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "unknown";
  }
}

export function frontendFingerprint() {
  const files = trackedInputFiles();
  return {
    source_fingerprint: hashFiles(files),
    input_files: files.map((path) => relative(projectRoot, path).replaceAll("\\", "/")),
  };
}

export function createFrontendManifest(outputDirectory) {
  const outputFiles = collectFiles(outputDirectory)
    .map((path) => relative(outputDirectory, path).replaceAll("\\", "/"))
    .filter((path) => path !== "build-manifest.json")
    .sort();
  return {
    schema_version: 1,
    build_id: `${gitSha().slice(0, 12)}-${frontendFingerprint().source_fingerprint.slice(0, 12)}`,
    git_sha: gitSha(),
    generated_at: new Date().toISOString(),
    ...frontendFingerprint(),
    output_files: outputFiles,
  };
}

if (process.argv.includes("--json")) {
  process.stdout.write(`${JSON.stringify(frontendFingerprint())}\n`);
}

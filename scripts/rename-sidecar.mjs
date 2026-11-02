import { execSync } from "node:child_process";
import { constants, copyFileSync, createReadStream, existsSync, mkdirSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const options = {};
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index];
  if (!["--source", "--target-dir", "--target-triple"].includes(key) || !process.argv[index + 1]) {
    throw new Error("Expected --source, --target-dir or --target-triple followed by a value.");
  }
  options[key] = process.argv[index + 1];
}
const source = options["--source"] ? resolve(options["--source"]) : join(root, "dist", "konggu-worker.exe");
if (!existsSync(source)) {
  throw new Error(`Missing PyInstaller output: ${source}`);
}

let targetTriple = options["--target-triple"] || "x86_64-pc-windows-msvc";
try {
  if (!options["--target-triple"]) {
  targetTriple = execSync("rustc -Vv", { encoding: "utf8" })
    .split(/\r?\n/)
    .find((line) => line.startsWith("host:"))
    ?.split(" ")[1]
    ?.trim() || targetTriple;
  }
} catch {
  // Rust is only required for the final Tauri build. Keep the Windows default for CI prep.
}
if (!/^[A-Za-z0-9_-]+$/.test(targetTriple)) throw new Error("Invalid Rust target triple.");

const targetDir = options["--target-dir"] ? resolve(options["--target-dir"]) : join(root, "src-tauri", "binaries");
mkdirSync(targetDir, { recursive: true });
const target = join(targetDir, `konggu-worker-${targetTriple}.exe`);
async function digest(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}
if (existsSync(target)) {
  if (await digest(source) !== await digest(target)) {
    throw new Error("A different sidecar already exists. Select a new --target-dir; previous artifacts are preserved.");
  }
} else {
  copyFileSync(source, target, constants.COPYFILE_EXCL);
}
console.log(`Sidecar copied for ${targetTriple}`);

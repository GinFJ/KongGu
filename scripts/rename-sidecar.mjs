import { execSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const source = join("dist", "konggu-worker.exe");
if (!existsSync(source)) {
  throw new Error(`Missing PyInstaller output: ${source}`);
}

let targetTriple = "x86_64-pc-windows-msvc";
try {
  targetTriple = execSync("rustc -Vv", { encoding: "utf8" })
    .split(/\r?\n/)
    .find((line) => line.startsWith("host:"))
    ?.split(" ")[1]
    ?.trim() || targetTriple;
} catch {
  // Rust is only required for the final Tauri build. Keep the Windows default for CI prep.
}

const targetDir = join("src-tauri", "binaries");
mkdirSync(targetDir, { recursive: true });
copyFileSync(source, join(targetDir, `konggu-worker-${targetTriple}.exe`));
console.log(`Sidecar copied for ${targetTriple}`);

import { defineConfig } from "vite";
import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

// PDF.js needs these files even when the PDF bytes are already local. Keep the
// original filenames because its worker resolves CMaps/fonts by their PDF names.
const pdfjsRoot = dirname(createRequire(import.meta.url).resolve("pdfjs-dist/package.json"));
const pdfAssets = new Map<string, Buffer>();
for (const directory of ["cmaps", "standard_fonts"]) {
  for (const name of readdirSync(join(pdfjsRoot, directory))) {
    pdfAssets.set(`${directory}/${name}`, readFileSync(join(pdfjsRoot, directory, name)));
  }
}

export default defineConfig({
  clearScreen: false,
  plugins: [{
    name: "offline-pdf-resources",
    generateBundle() {
      for (const [name, source] of pdfAssets) {
        this.emitFile({ type: "asset", fileName: `pdfjs/${name}`, source });
      }
    },
    configureServer(server) {
      server.middlewares.use("/pdfjs/", (request, response, next) => {
        const key = (request.url || "").split("?")[0].replace(/^\/+/, "");
        const data = pdfAssets.get(key);
        if (!data) return next();
        response.setHeader("Content-Type", "application/octet-stream");
        response.end(data);
      });
    }
  }],
  server: {
    strictPort: true,
    port: 1420
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "es2020",
    outDir: "dist",
    emptyOutDir: true
  }
});

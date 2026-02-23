import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, "src/main.ts"),
      name: "LerimGraphExplorer",
      formats: ["iife"],
      fileName: () => "graph-explorer.js",
    },
    outDir: resolve(__dirname, "../../assets/graph-explorer"),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        assetFileNames: "graph-explorer.css",
      },
    },
    sourcemap: false,
    minify: true,
  },
});

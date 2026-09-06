import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";

import packageJson from "./package.json" with { type: "json" };

/**
 * The IIFE build is what a script tag would serve, so it is the artifact
 * scripts/measure-size.js weighs. The ESM build is the same code for a bundler.
 * Both are self-contained: a script tag cannot resolve bare imports.
 */
export default defineConfig({
  define: {
    __NEXOVUE_SDK_VERSION__: JSON.stringify(packageJson.version),
  },
  build: {
    target: "es2020",
    sourcemap: true,
    minify: true,
    lib: {
      entry: fileURLToPath(new URL("./src/index.ts", import.meta.url)),
      name: "nexovue",
      formats: ["es", "iife"],
      fileName: (format) => (format === "iife" ? "nexovue-rum.min.js" : "index.js"),
    },
  },
});

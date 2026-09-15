import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";

import packageJson from "./package.json" with { type: "json" };

/**
 * Attribution that has to survive minification.
 *
 * Because the bundles are self-contained (below), three Apache-2.0 works are
 * compiled into them, and that licence's attribution follows the artifact
 * rather than the source tree. The ASF names minified JavaScript as the case
 * where the notice belongs in the file itself; `/*!` is the marker every
 * minifier preserves. NOTICE carries the full text.
 */
const ATTRIBUTION = `/*! nexovue browser SDK — Copyright 2026 ANANDHARAJ A — Apache-2.0
 * Bundles OpenTelemetry JS (Copyright The OpenTelemetry Authors) and
 * web-vitals (Copyright Google LLC), both Apache-2.0.
 * http://www.apache.org/licenses/LICENSE-2.0
 */`;

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
    rollupOptions: {
      // `comments.legal` defaults to `!minify`, so without it the minifier
      // drops the banner and the shipped file carries no attribution at all.
      output: { banner: ATTRIBUTION, comments: { legal: true } },
    },
  },
});

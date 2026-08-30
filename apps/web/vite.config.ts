import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vite";

/** Faces the first screen renders in. Everything else may arrive late. */
const CRITICAL_FONT_FACES = [
  "ibm-plex-sans-latin-400-normal",
  "ibm-plex-sans-latin-500-normal",
  "ibm-plex-mono-latin-400-normal",
];

/**
 * Emits `<link rel="preload">` for the critical faces. They live inside @nexovue/ui and
 * are content-hashed on the way into the bundle, so their final names exist only here.
 * Build only: in dev the fonts are served unhashed straight from the package.
 */
function preloadCriticalFonts(): Plugin {
  return {
    name: "nexovue:preload-critical-fonts",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, context) {
        if (!context.bundle) return;

        return Object.values(context.bundle)
          .filter((output) => output.type === "asset" && output.fileName.endsWith(".woff2"))
          .filter((output) => CRITICAL_FONT_FACES.some((face) => output.fileName.includes(face)))
          .map((output) => ({
            tag: "link",
            injectTo: "head-prepend" as const,
            attrs: {
              rel: "preload",
              as: "font",
              type: "font/woff2",
              href: `/${output.fileName}`,
              crossorigin: "anonymous",
            },
          }));
      },
    },
  };
}

export default defineConfig({
  plugins: [tailwindcss(), react(), preloadCriticalFonts()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // The API is a separate service; the browser only ever knows the /api/v1/ path.
    proxy: {
      "/api": {
        target: process.env.NEXOVUE_API_ORIGIN ?? "http://localhost:18000",
        changeOrigin: true,
        // The log tail is a WebSocket under /api. Without this the upgrade request
        // is answered by the dev server instead of forwarded, and the handshake
        // times out with nothing in the console to say why.
        ws: true,
      },
    },
  },
});

import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { readSetupDocs } from "./setup-docs-plugin.js";

const REPOSITORY_ROOT = fileURLToPath(new URL("../../..", import.meta.url));
const SETUP_ROOT = join(REPOSITORY_ROOT, "docs/product/setup");

function slugsOnDisk(root: string, base = root): string[] {
  return readdirSync(root)
    .sort()
    .flatMap((entry) => {
      const path = join(root, entry);
      if (statSync(path).isDirectory()) return slugsOnDisk(path, base);
      return entry.endsWith(".md") ? [relative(base, path).replace(/\.md$/, "")] : [];
    });
}

describe("compiling the setup documentation", () => {
  const docs = readSetupDocs(REPOSITORY_ROOT);

  it("publishes every page that exists on disk", () => {
    /* A page written and not published is worse than one not written: it looks
       done in the repository and cannot be reached by the customer it is for. */
    expect(docs.map((doc) => doc.slug).sort()).toEqual(slugsOnDisk(SETUP_ROOT).sort());
  });

  it("renders every page to something, so none is a blank address", () => {
    for (const doc of docs) {
      expect(doc.html.length, `${doc.slug} rendered empty`).toBeGreaterThan(100);
      expect(doc.html, `${doc.slug} has no heading`).toContain("<h1");
    }
  });

  it("leaves no link pointing at a file", () => {
    /* `[browser.md](browser.md)` is correct in a repository and wrong twice on a
       website: the href is a 404 and the text names a file nobody can see. */
    for (const doc of docs) {
      expect(doc.html, `${doc.slug}`).not.toMatch(/href="[^"]*\.md"/);
      expect(doc.html, `${doc.slug}`).not.toMatch(/>[^<]*\.md</);
    }
  });

  it("names a link after the page it points at", () => {
    const index = docs.find((doc) => doc.slug === "index");

    expect(index?.html).toContain('href="/docs/already-instrumented"');
    expect(index?.html).toContain("If you already export OpenTelemetry");
  });

  it("resolves a link that climbs out of a subfolder", () => {
    const ecs = docs.find((doc) => doc.slug === "platforms/aws-ecs");

    expect(ecs?.html).toContain('href="/docs/collector"');
  });

  it("gives headings ids, so a link to a section reaches one", () => {
    /* markdown-it emits none, so `index.md`'s `(#languages)` pointed at nothing —
       a link that silently does nothing is worse than no link. */
    const index = docs.find((doc) => doc.slug === "index");

    expect(index?.html).toContain('id="languages"');
  });

  it("does not let a page carry raw markup", () => {
    /* The screen renders this with `dangerouslySetInnerHTML`. The plugin sets
       `html: false`, and this is what keeps that true as pages are written. */
    for (const doc of docs) {
      expect(doc.html, `${doc.slug}`).not.toContain("<script");
    }
  });
});

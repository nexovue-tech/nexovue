import { describe, expect, it } from "vitest";

import { DOCS, docBySlug, docGroups, INDEX_SLUG } from "./pages";

/* What the pages *are* is proved in `build/setup-docs-plugin.test.ts`, where the
   filesystem belongs. This is about how they are arranged, which is the part this
   module owns. */

describe("arranging the published pages", () => {
  it("puts every page in a named group, and the front page first", () => {
    const groups = docGroups();

    expect(groups.map((group) => group.label)).toEqual([
      "Start here",
      "Your language",
      "Where it runs",
      "Alerts",
      "Profiling",
    ]);
    expect(groups[0].pages[0].slug).toBe(INDEX_SLUG);
  });

  it("loses no page to the grouping", () => {
    /* A group nobody named still appears under its folder name: an unlisted page
       is worse than an ugly heading, because it then exists and cannot be reached. */
    expect(docGroups().flatMap((group) => group.pages)).toHaveLength(DOCS.length);
  });

  it("finds a page by the slug its route carries", () => {
    expect(docBySlug("languages/go")?.title).toBe("Go");
    expect(docBySlug("nothing-here")).toBeUndefined();
  });

  it("takes each page title from its own heading", () => {
    expect(docBySlug(INDEX_SLUG)?.title).toBe("Sending data to nexovue");
  });
});

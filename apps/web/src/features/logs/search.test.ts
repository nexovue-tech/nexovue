import { describe, expect, it } from "vitest";

import { MAX_SEARCH_PHRASE, searchPhrase } from "./search";

/** F4 T3.3: the phrase a chat step's link carries to `/logs`, read into the search box. */
describe("a search phrase in the address", () => {
  it("is read trimmed, and nothing when blank, absent or not text", () => {
    expect(searchPhrase("  timed out ")).toBe("timed out");
    expect(searchPhrase("   ")).toBeUndefined();
    expect(searchPhrase(undefined)).toBeUndefined();
    expect(searchPhrase(42)).toBeUndefined();
  });

  it("is refused past the longest phrase chat may search for", () => {
    expect(searchPhrase("a".repeat(MAX_SEARCH_PHRASE))).toHaveLength(MAX_SEARCH_PHRASE);
    expect(searchPhrase("a".repeat(MAX_SEARCH_PHRASE + 1))).toBeUndefined();
  });
});

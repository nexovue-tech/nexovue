import { describe, expect, it } from "vitest";

import { DIFF_WORDS, flagAnchor, visibleEscapes } from "./fix-page";

/**
 * The diff's pure parts (F5 plan T4.6; spec AC36, AC18): hidden characters show as visible
 * codes in every line, and a flag's anchor is built from Nexovue's own values.
 */

describe("visible escapes (AC36)", () => {
  it("shows bidirectional, zero-width and control characters as their codes", () => {
    const pieces = visibleEscapes("a\u202eb\u200bc\u0000d\u{E0041}");

    expect(pieces).toEqual([
      { text: "a", escaped: false },
      { text: "\\u{202E}", escaped: true },
      { text: "b", escaped: false },
      { text: "\\u{200B}", escaped: true },
      { text: "c", escaped: false },
      { text: "\\u{0}", escaped: true },
      { text: "d", escaped: false },
      { text: "\\u{E0041}", escaped: true },
    ]);
  });

  it("leaves tabs and ordinary text, accents included, as they are", () => {
    expect(visibleEscapes("\tcafé = 1")).toEqual([{ text: "\tcafé = 1", escaped: false }]);
  });
});

describe("flags and counts", () => {
  it("a flag's anchor holds no character a path could use to break out", () => {
    expect(flagAnchor('svc/a "b".py', "new", 5)).toBe("fix-line-new-5-svc-a--b--py");
  });

  it("counts read as plus and minus", () => {
    expect(DIFF_WORDS.counts(6, 2)).toBe("+6 −2");
  });
});

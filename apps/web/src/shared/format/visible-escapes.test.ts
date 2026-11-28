import { describe, expect, it } from "vitest";

import { visibleEscapes } from "./visible-escapes";

/**
 * F20 FR61, AC74, AC123: a name holding a direction override or a zero-width character
 * can look exactly like another, so every telemetry value and stored free text shows
 * such characters as visible escapes. The raw value is what is bound and stored; this is
 * for display only.
 */
describe("visibleEscapes", () => {
  it("shows a right-to-left override as a visible escape", () => {
    expect(visibleEscapes("check\u202Eout")).toBe("check[U+202E]out");
  });

  it.each([
    ["zero-width space", "check\u200Bout", "check[U+200B]out"],
    ["isolate", "\u2066checkout", "[U+2066]checkout"],
    ["a tag character, past the basic plane", "a\u{E0041}b", "a[U+E0041]b"],
    ["NUL", "a\u0000b", "a[U+0000]b"],
    ["a bell", "a\u0007b", "a[U+0007]b"],
    ["a line separator", "a\u2028b", "a[U+2028]b"],
  ])("escapes a %s", (_, raw, shown) => {
    expect(visibleEscapes(raw)).toBe(shown);
  });

  it("leaves ordinary text, accents and markup exactly as written", () => {
    const text = "Café <img src=x onerror=alert(1)> **bold** https://x 東京";
    expect(visibleEscapes(text)).toBe(text);
  });

  it("escapes a line break in a name, and keeps it in a note when asked", () => {
    expect(visibleEscapes("check\nout")).toBe("check[U+000A]out");
    expect(visibleEscapes("line one\nline two", { keepLineBreaks: true })).toBe(
      "line one\nline two",
    );
  });
});

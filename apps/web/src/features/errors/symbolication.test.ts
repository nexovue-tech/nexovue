import { describe, expect, it } from "vitest";

import { canSymbolicate, framesOf } from "./symbolication";

/**
 * Real stacks, copied from the three engines rather than invented.
 *
 * The formats are why this parsing is not hand-rolled: V8 writes `at fn (file)`,
 * SpiderMonkey and JavaScriptCore write `fn@file`, and each has frames with no
 * file at all. A parser that handles only the first silently symbolicates
 * nothing for half the web.
 */
const CHROME = [
  "TypeError: Cannot read properties of undefined (reading 'total')",
  "    at renderTotal (https://shop.example.com/static/js/main.a3f9.js:1:84213)",
  "    at https://shop.example.com/static/js/main.a3f9.js:1:52180",
  "    at Array.map (<anonymous>)",
].join("\n");

const FIREFOX = [
  "renderTotal@https://shop.example.com/static/js/main.a3f9.js:1:84213",
  "checkout@https://shop.example.com/static/js/main.a3f9.js:1:52180",
].join("\n");

const SAFARI = [
  "renderTotal@https://shop.example.com/static/js/main.a3f9.js:1:84213",
  "global code@https://shop.example.com/static/js/main.a3f9.js:1:11",
].join("\n");

describe("parsing a minified stack into frames", () => {
  it("reads V8's two frame shapes, named and anonymous", () => {
    const frames = framesOf(CHROME);

    expect(frames).toEqual([
      { file: "https://shop.example.com/static/js/main.a3f9.js", line: 1, column: 84213 },
      { file: "https://shop.example.com/static/js/main.a3f9.js", line: 1, column: 52180 },
    ]);
  });

  it("drops an eval frame, whose file parses to empty rather than null", () => {
    // Copied verbatim from a real headless Chromium stack. Left in, the API
    // rejects the whole request as invalid and one eval frame costs the trace
    // its symbolication entirely — which is how this was found.
    const withEval = [
      "TypeError: Cannot read properties of undefined (reading 'lines')",
      "    at t (http://localhost:15173/static/js/checkout.min.js:1:111)",
      "    at eval (eval at evaluate (:311:30), <anonymous>:1:37)",
    ].join("\n");

    const frames = framesOf(withEval);

    expect(frames).toHaveLength(1);
    expect(frames.every((frame) => frame.file !== "")).toBe(true);
  });

  it("drops a native frame, which has nothing to look up", () => {
    // `at Array.map (<anonymous>)` has no line to map. Sending it would spend a
    // map fetch to be told what we already know.
    expect(framesOf(CHROME)).toHaveLength(2);
  });

  it.each([
    ["Firefox", FIREFOX],
    ["Safari", SAFARI],
  ])("reads %s's fn@file form", (_engine, stack) => {
    const frames = framesOf(stack);

    expect(frames).toHaveLength(2);
    expect(frames[0]).toEqual({
      file: "https://shop.example.com/static/js/main.a3f9.js",
      line: 1,
      column: 84213,
    });
  });

  it("keeps frame order, so the trace still reads top-down", () => {
    expect(framesOf(CHROME).map((frame) => frame.column)).toEqual([84213, 52180]);
  });

  it("returns nothing for a stack with no positions in it at all", () => {
    expect(framesOf("Error: something went wrong")).toEqual([]);
    expect(framesOf("")).toEqual([]);
  });
});

describe("deciding whether to ask at all", () => {
  it("needs both a stack and a release", () => {
    expect(canSymbolicate(CHROME, "2026.09.06-1")).toBe(true);
    // The browser SDK's serviceVersion is optional, so this is the common case.
    expect(canSymbolicate(CHROME, "")).toBe(false);
    expect(canSymbolicate("", "2026.09.06-1")).toBe(false);
  });

  it("refuses a stack that parses to no frames", () => {
    // A Python traceback reaching the browser door's endpoint would be one.
    const python = [
      "Traceback (most recent call last):",
      '  File "/app/payments.py", line 88, in authorize',
      "AuthorizationDeclined: card declined",
    ].join("\n");

    expect(canSymbolicate(python, "2026.09.06-1")).toBe(false);
  });

  it("treats whitespace as absent, not as present", () => {
    expect(canSymbolicate(CHROME, "   ")).toBe(false);
  });
});

import { describe, expect, it } from "vitest";

import type { FunctionChange, FunctionShare } from "./api";
import { changeReadingOf, isStale, readingOf } from "./profile-reading";

const fn = (name: string, self: number, unsymbolized = false): FunctionShare => ({
  name,
  file: "",
  unsymbolized,
  self_value: self,
  total: self,
});

const change = (name: string, before: number, after: number): FunctionChange => ({
  name,
  file: "",
  unsymbolized: false,
  baseline_self: before,
  comparison_self: after,
  baseline_total: before,
  comparison_total: after,
  change: after - before,
});

describe("the sentence a profile leads with (principles rule 1)", () => {
  it("names the function that spent the most itself, and its share", () => {
    expect(
      readingOf("checkout", "cpu", 100, [fn("json.Marshal", 30), fn("sha256.block", 60)]),
    ).toBe("sha256.block accounts for 60 % of checkout's CPU time — more than any other function.");
  });

  it("says nothing stands out when the cost is spread, rather than crowning a 3 % function", () => {
    const spread = Array.from({ length: 40 }, (_, index) => fn(`f${index}`, 2.5));
    expect(readingOf("cart", "cpu", 100, spread)).toBe(
      "No single function stands out in cart's CPU time: the largest, f0, is 2.5 % of it.",
    );
  });

  it("says unsymbolized native code is native code, not a function of yours", () => {
    expect(readingOf("payment", "samples", 10, [fn("libc.so.6", 8, true)])).toContain(
      "unsymbolized native code in libc.so.6 accounts for 80 %",
    );
  });

  it("says what grew and what shrank between two profiles, naming both sides", () => {
    expect(
      changeReadingOf("From version 1.0.0 to 2.0.0", [
        change("sha256.block", 0.2, 0.4),
        change("json.Marshal", 0.8, 0.6),
      ]),
    ).toBe(
      "From version 1.0.0 to 2.0.0, sha256.block grew most, from 20 % to 40 % of the total; json.Marshal shrank most, from 80 % to 60 %.",
    );
  });

  it("says nothing moved when nothing did, rather than naming noise", () => {
    expect(changeReadingOf("Compared with the window before", [change("a", 0.5, 0.502)])).toBe(
      "Compared with the window before, no function's share moved by more than half a point.",
    );
  });

  it("says a function that is the whole profile is all of it, not 'more than any other'", () => {
    expect(readingOf("this span", "cpu", 90, [fn("sha256.block", 90)])).toBe(
      "sha256.block accounts for all of this span's CPU time.",
    );
  });

  it("calls a profile older than ten minutes stale — the profiler may have stopped (US17.8)", () => {
    const now = Date.parse("2026-09-19T12:00:00Z");
    expect(isStale("2026-09-19T11:49:00Z", now)).toBe(true);
    expect(isStale("2026-09-19T11:55:00Z", now)).toBe(false);
  });
});

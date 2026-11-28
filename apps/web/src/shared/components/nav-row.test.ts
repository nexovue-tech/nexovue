import { describe, expect, it } from "vitest";

import { NAV_ROW, NAV_ROW_ACTIVE, NAV_ROW_IDLE } from "./nav-row";

const BORDER_COLOUR = /(^|\s)border(-l)?-(transparent|brand|line\S*)(\s|$)/;

describe("a menu row's left rule (UX review, 2026-11-28)", () => {
  it("leaves the rule's colour to the row's state, so the chosen green is never overridden", () => {
    // Both colours on one element let the stylesheet's order decide, and transparent won:
    // the chosen row showed no green rule at all.
    expect(NAV_ROW).not.toMatch(BORDER_COLOUR);
    expect(NAV_ROW_IDLE).toMatch(BORDER_COLOUR);
    expect(NAV_ROW_ACTIVE).toMatch(BORDER_COLOUR);
  });
});

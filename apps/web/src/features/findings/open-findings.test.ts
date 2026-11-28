import { describe, expect, it } from "vitest";

import { openFindingsBadge } from "./open-findings";

describe("the sidebar's count beside Findings (sketch G)", () => {
  it("shows nothing when nothing is open", () => {
    expect(openFindingsBadge({ open: 0, critical: 0 })).toBeNull();
  });

  it("counts what is open, critical when any is", () => {
    expect(openFindingsBadge({ open: 1, critical: 0 })).toEqual({
      text: "1 open",
      label: "1 open finding",
      severity: "warning",
    });
    expect(openFindingsBadge({ open: 12, critical: 2 })).toEqual({
      text: "9+",
      label: "12 open findings",
      severity: "critical",
    });
  });
});

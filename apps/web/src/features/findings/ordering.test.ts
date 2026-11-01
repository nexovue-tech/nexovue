import { describe, expect, it } from "vitest";

import type { Finding } from "./api";
import { needsAttention, orderFindings } from "./ordering";

const finding = (overrides: Partial<Finding> & { id: string }): Finding => ({
  project_id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a00aa",
  scope: "service",
  service_name: "checkout-api",
  title: "Something changed",
  severity: "warning",
  state: "open",
  closed_reason: null,
  muted: false,
  started_at: "2026-09-13T10:00:00Z",
  closed_at: null,
  acknowledged_at: null,
  signals: [],
  trend: [],
  ...overrides,
});

const ids = (findings: Finding[]) => findings.map((found) => found.id);

describe("F1 AC1.5: open, then acknowledged, then closed; then severity, then recency", () => {
  it("puts anything still wrong above anything already fixed", () => {
    // Even when the closed one is far newer. A feed that leads with good news
    // buries the reason somebody opened it.
    const ordered = orderFindings([
      finding({ id: "fixed", state: "closed", closed_at: "2026-09-13T11:59:00Z" }),
      finding({ id: "broken", started_at: "2026-09-13T08:00:00Z" }),
    ]);

    expect(ids(ordered)).toEqual(["broken", "fixed"]);
  });

  it("keeps an acknowledged finding above a closed one", () => {
    // Acknowledged means somebody saw it, not that anybody fixed it.
    const ordered = orderFindings([
      finding({ id: "fixed", state: "closed", closed_at: "2026-09-13T11:00:00Z" }),
      finding({ id: "seen", state: "acknowledged" }),
    ]);

    expect(ids(ordered)).toEqual(["seen", "fixed"]);
  });

  it("puts an open finding nobody has seen above an acknowledged one", () => {
    // Even a newer, critical acknowledged one: somebody already has that.
    const ordered = orderFindings([
      finding({ id: "seen", state: "acknowledged", severity: "critical" }),
      finding({ id: "unseen", started_at: "2026-09-13T08:00:00Z" }),
    ]);

    expect(ids(ordered)).toEqual(["unseen", "seen"]);
  });

  it("ranks critical above warning inside the same tier", () => {
    const ordered = orderFindings([
      finding({ id: "warn", severity: "warning", started_at: "2026-09-13T11:00:00Z" }),
      finding({ id: "crit", severity: "critical", started_at: "2026-09-13T09:00:00Z" }),
    ]);

    expect(ids(ordered)).toEqual(["crit", "warn"]);
  });

  it("breaks a tie by recency, newest first", () => {
    const ordered = orderFindings([
      finding({ id: "older", started_at: "2026-09-13T09:00:00Z" }),
      finding({ id: "newer", started_at: "2026-09-13T11:00:00Z" }),
    ]);

    expect(ids(ordered)).toEqual(["newer", "older"]);
  });

  it("orders closed findings by when they closed, not when they started", () => {
    // The question about the quiet half of the feed is what stopped being wrong
    // and when. Ordering by start date interleaves them by something nobody reads.
    const ordered = orderFindings([
      finding({
        id: "started-first-fixed-last",
        state: "closed",
        started_at: "2026-09-13T06:00:00Z",
        closed_at: "2026-09-13T11:00:00Z",
      }),
      finding({
        id: "started-last-fixed-first",
        state: "closed",
        started_at: "2026-09-13T10:00:00Z",
        closed_at: "2026-09-13T10:30:00Z",
      }),
    ]);

    expect(ids(ordered)).toEqual(["started-first-fixed-last", "started-last-fixed-first"]);
  });

  it("does not reorder the caller's array", () => {
    const given = [finding({ id: "b" }), finding({ id: "a", severity: "critical" })];

    orderFindings(given);

    expect(ids(given)).toEqual(["b", "a"]);
  });

  it("survives an unparseable timestamp instead of scrambling the feed", () => {
    // NaN comparisons make a sort's output whatever the engine happened to do.
    const ordered = orderFindings([
      finding({ id: "broken-clock", started_at: "not a date" }),
      finding({ id: "fine", started_at: "2026-09-13T09:00:00Z" }),
    ]);

    expect(ids(ordered)).toEqual(["fine", "broken-clock"]);
  });
});

describe("whether anything needs somebody", () => {
  it("is false when every finding is closed", () => {
    expect(needsAttention([finding({ id: "a", state: "closed" })])).toBe(false);
  });

  it("is false when there are no findings at all", () => {
    expect(needsAttention([])).toBe(false);
  });

  it("is true while one is open", () => {
    expect(needsAttention([finding({ id: "a" })])).toBe(true);
  });

  it("is true while one is merely acknowledged", () => {
    expect(needsAttention([finding({ id: "a", state: "acknowledged" })])).toBe(true);
  });
});

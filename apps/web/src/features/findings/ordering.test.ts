import { describe, expect, it } from "vitest";

import type { Finding } from "./api";
import orderFixture from "./feed-order.fixture.json";
import { orderFindings } from "./ordering";

const finding = (overrides: Partial<Finding> & { id: string }): Finding => ({
  project_id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a00aa",
  scope: "service",
  service_name: "checkout-api",
  title: "Something changed",
  severity: "warning",
  state: "open",
  closed_reason: null,
  service_snoozed_until: null,
  service_muted_until: null,
  muted: false,
  started_at: "2026-09-13T10:00:00Z",
  closed_at: null,
  acknowledged_at: null,
  signals: [],
  trend: [],
  case_state: null,
  case_resolution: null,
  case_opened_at: null,
  case_resolved_at: null,
  root_finding_id: null,
  case_signal: "open",
  open_joined_finding_id: null,
  open_joined_since: null,
  // As the API sends it: the row's own severity unless a joined finding is worse.
  case_severity: overrides.severity ?? "warning",
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

interface OrderFixtureEntry {
  id: string;
  service: string;
  state: "open" | "closed";
  acknowledged: boolean;
  severity: Finding["severity"];
  case_state: Finding["case_state"];
  started_minutes_ago: number;
  closed_minutes_ago?: number;
  root?: string;
}

const FIXTURE_NOW = Date.parse("2026-11-02T14:00:00Z");
const minutesAgo = (minutes: number) => new Date(FIXTURE_NOW - minutes * 60_000).toISOString();

/** Each fixture row as the API would send it, its case fields derived as `to_finding` does. */
function asSent(entries: readonly OrderFixtureEntry[]): Finding[] {
  const openReturns = new Map(
    entries
      .filter((entry) => entry.root !== undefined && entry.state === "open")
      .map((entry) => [entry.root as string, entry]),
  );
  return entries.map((entry) => {
    const back = openReturns.get(entry.id);
    return finding({
      id: entry.id,
      service_name: entry.service,
      severity: entry.severity,
      state: entry.state === "closed" ? "closed" : entry.acknowledged ? "acknowledged" : "open",
      started_at: minutesAgo(entry.started_minutes_ago),
      closed_at:
        entry.closed_minutes_ago === undefined ? null : minutesAgo(entry.closed_minutes_ago),
      case_state: entry.case_state,
      root_finding_id: entry.root ?? null,
      case_signal: entry.state === "open" || back !== undefined ? "open" : "closed",
      case_severity: back?.severity === "critical" ? "critical" : entry.severity,
      open_joined_finding_id: back?.id ?? null,
      open_joined_since: back ? minutesAgo(back.started_minutes_ago) : null,
    });
  });
}

describe("F22 FR21, AC27: the console's order is the server's", () => {
  it("puts the shared test set in the server's order", () => {
    // `test_findings_api_live.py` holds the server's rank to this same file.
    const entries = orderFixture.findings as OrderFixtureEntry[];
    // Reversed first, so the result cannot be the input order by luck.
    expect(ids(orderFindings(asSent(entries).reverse()))).toEqual(orderFixture.expected_order);
  });

  it("keeps a recovered root whose finding came back in the open tier, at the worse severity", () => {
    // AC59: never "Recovered" as the current state while a joined finding is open.
    const ordered = orderFindings([
      finding({ id: "acked", state: "acknowledged", severity: "critical" }),
      finding({
        id: "came-back",
        state: "closed",
        closed_at: "2026-09-13T01:00:00Z",
        case_state: "investigating",
        case_signal: "open",
        case_severity: "critical",
        open_joined_finding_id: "returning",
      }),
    ]);

    expect(ids(ordered)).toEqual(["came-back", "acked"]);
  });

  it("puts a closed finding with an active case above every other closed one", () => {
    const ordered = orderFindings([
      finding({ id: "just-fixed", state: "closed", closed_at: "2026-09-13T11:59:00Z" }),
      finding({
        id: "still-worked",
        state: "closed",
        closed_at: "2026-09-10T11:00:00Z",
        case_state: "investigating",
        case_signal: "closed",
      }),
    ]);

    expect(ids(ordered)).toEqual(["still-worked", "just-fixed"]);
  });
});

import { describe, expect, it } from "vitest";

import type { StatementCost } from "./api";
import {
  describeCallsInWindow,
  describeHashLookup,
  formatCallsInWindow,
  formatServer,
  shortHash,
} from "./statement-format";

function statement(overrides: Partial<StatementCost> = {}): StatementCost {
  return {
    query_hash: "-4913072044542253312",
    namespace: "orders",
    server: { address: "db.internal", port: 5432 },
    db_system: "postgresql",
    latest_mean_ms: 12.5,
    worst_mean_ms: 40,
    calls_total: 90_000,
    calls_in_window: 1_200,
    readings: 6,
    latest_reading_at: "2026-08-30T09:42:56.449Z",
    ...overrides,
  };
}

describe("formatCallsInWindow", () => {
  it("reports the rise once two polls make a rise measurable", () => {
    expect(formatCallsInWindow(statement({ readings: 2, calls_in_window: 1_200 }))).toBe("1,200");
  });

  it("is a rule, not a zero, when only one poll has happened", () => {
    // max(calls) - min(calls) over one reading is 0, and "0 calls" would read as
    // "this statement was never called" when the truth is "we have looked once".
    expect(formatCallsInWindow(statement({ readings: 1, calls_in_window: 0 }))).toBe("—");
  });

  it("shows a genuine zero when two polls agree nothing was called", () => {
    expect(formatCallsInWindow(statement({ readings: 4, calls_in_window: 0 }))).toBe("0");
  });
});

describe("describeCallsInWindow", () => {
  it("explains the empty cell instead of leaving it a mystery", () => {
    const said = describeCallsInWindow(statement({ readings: 1, calls_total: 90_000 }));

    expect(said).toContain("Polled once");
    expect(said).toContain("90,000");
  });

  it("names both the window count and the counter's own total", () => {
    const said = describeCallsInWindow(
      statement({ readings: 6, calls_in_window: 1_200, calls_total: 90_000 }),
    );

    expect(said).toContain("1,200");
    expect(said).toContain("90,000");
  });
});

describe("formatServer", () => {
  it("reads as address and port", () => {
    expect(formatServer({ address: "db.internal", port: 5432 })).toBe("db.internal:5432");
  });

  it("drops a port the poller never reported rather than printing :0", () => {
    expect(formatServer({ address: "db.internal", port: 0 })).toBe("db.internal");
  });

  it("is a rule when there is no address at all", () => {
    expect(formatServer({ address: "", port: 5432 })).toBe("—");
  });
});

describe("shortHash", () => {
  it("trims a queryid to something scannable in a column", () => {
    expect(shortHash("-4913072044542253312")).toBe("-49130720445…");
  });

  it("leaves a short hash whole", () => {
    expect(shortHash("12345")).toBe("12345");
  });
});

describe("describeHashLookup", () => {
  it("hands over the exact query that turns the hash back into text", () => {
    // We hold no statement text by design, so the screen's job is the lookup.
    const said = describeHashLookup(statement());

    expect(said).toContain("pg_stat_statements");
    expect(said).toContain("-4913072044542253312");
    expect(said).toContain("orders");
    expect(said).toContain("db.internal:5432");
  });
});

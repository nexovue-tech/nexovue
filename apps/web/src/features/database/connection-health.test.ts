import { describe, expect, it } from "vitest";

import type { ConnectionStateCount } from "./api";
import { describeState, groupByServer, holdsAnOpenTransaction } from "./connection-health";

function count(overrides: Partial<ConnectionStateCount> = {}): ConnectionStateCount {
  return {
    state: "idle",
    server: { address: "db.internal", port: 5432 },
    latest_backends: 4,
    peak_backends: 6,
    readings: 12,
    latest_reading_at: "2026-08-30T09:42:56.449Z",
    ...overrides,
  };
}

describe("groupByServer", () => {
  it("collects the states of one server into one entry", () => {
    const servers = groupByServer([
      count({ state: "idle", latest_backends: 4 }),
      count({ state: "active", latest_backends: 3 }),
    ]);

    expect(servers).toHaveLength(1);
    expect(servers[0].label).toBe("db.internal:5432");
    expect(servers[0].latestTotal).toBe(7);
  });

  it("keeps two servers apart", () => {
    const servers = groupByServer([
      count({ server: { address: "a.internal", port: 5432 }, latest_backends: 2 }),
      count({ server: { address: "b.internal", port: 5432 }, latest_backends: 9 }),
    ]);

    expect(servers.map((server) => server.label)).toEqual(["b.internal:5432", "a.internal:5432"]);
  });

  it("orders the states of a server by how many backends are in them", () => {
    const servers = groupByServer([
      count({ state: "idle", latest_backends: 2 }),
      count({ state: "active", latest_backends: 8 }),
    ]);

    expect(servers[0].states.map((state) => state.state)).toEqual(["active", "idle"]);
  });

  it("reports the largest single state reading, never a summed peak", () => {
    // The states were not sampled at the same instant, so 6 + 9 describes a moment
    // that never happened. 9 is a number somebody actually observed.
    const servers = groupByServer([
      count({ state: "idle", peak_backends: 6 }),
      count({ state: "active", peak_backends: 9 }),
    ]);

    expect(servers[0].peakOfAnyState).toBe(9);
  });

  it("flags a server holding open transactions", () => {
    const servers = groupByServer([
      count({ state: "idle in transaction", latest_backends: 3 }),
      count({ state: "idle", latest_backends: 1 }),
    ]);

    expect(servers[0].holdsOpenTransactions).toBe(true);
  });

  it("does not flag a state that is present but empty", () => {
    const servers = groupByServer([count({ state: "idle in transaction", latest_backends: 0 })]);

    expect(servers[0].holdsOpenTransactions).toBe(false);
  });

  it("has nothing to group when nothing was polled", () => {
    expect(groupByServer([])).toEqual([]);
  });
});

describe("holdsAnOpenTransaction", () => {
  it("names the two states that hold locks and hold back vacuum", () => {
    expect(holdsAnOpenTransaction("idle in transaction")).toBe(true);
    expect(holdsAnOpenTransaction("idle in transaction (aborted)")).toBe(true);
  });

  it("leaves the ordinary states alone — health is the absence of colour", () => {
    expect(holdsAnOpenTransaction("active")).toBe(false);
    expect(holdsAnOpenTransaction("idle")).toBe(false);
  });
});

describe("describeState", () => {
  it("says why an open transaction matters rather than only colouring it", () => {
    expect(describeState("idle in transaction")).toContain("vacuum");
  });

  it("still says something about a state we do not recognise", () => {
    expect(describeState("fastpath function call")).toContain("pg_stat_activity");
  });
});

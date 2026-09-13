/**
 * Backends by state, arranged the way the question is asked.
 *
 * The API returns one row per (state, server) pair, because that is how the readings
 * are stored. A person asks about one database at a time, so the rows are grouped back
 * into servers here.
 *
 * **There is no "connections are too high" line on this screen, and there must not be
 * one.** That judgement needs `max_connections`, and the poller does not read it — so
 * any threshold drawn here would be a number we made up. What the screen can say
 * truthfully is which states are present, since two of them are a known pathology
 * regardless of the count.
 */

import type { ConnectionStateCount } from "./api";
import { formatServer } from "./statement-format";

/**
 * A backend idle inside an open transaction holds its locks and pins the oldest
 * transaction id, which blocks vacuum from reclaiming dead rows. That is a property of
 * the state, not of how many there are, so it needs no threshold to be worth flagging.
 */
const HELD_TRANSACTION_STATES: ReadonlySet<string> = new Set([
  "idle in transaction",
  "idle in transaction (aborted)",
]);

export function holdsAnOpenTransaction(state: string): boolean {
  return HELD_TRANSACTION_STATES.has(state);
}

/** Why the state is flagged, said plainly rather than left as a colour. */
export function describeState(state: string): string {
  if (state === "idle in transaction") {
    return "Idle inside an open transaction: these backends hold their locks and hold back vacuum until the transaction ends.";
  }
  if (state === "idle in transaction (aborted)") {
    return "Idle inside a transaction that has already failed: the work is lost, and the locks are held until it is rolled back.";
  }
  if (state === "active") return "Running a statement right now.";
  if (state === "idle") return "Connected with no transaction open.";
  return `pg_stat_activity reported this backend state.`;
}

export interface ServerConnections {
  /** `address:port`, and the key the list is rendered by. */
  label: string;
  states: readonly ConnectionStateCount[];
  latestTotal: number;
  /** The highest single reading of any one state, not the peak of the sum. */
  peakOfAnyState: number;
  latestReadingAt: string;
  /** True when any backend was idle inside an open transaction. */
  holdsOpenTransactions: boolean;
}

/**
 * One entry per database server, busiest first.
 *
 * `peakOfAnyState` is deliberately not a peak total: the states were not necessarily
 * sampled at the same instant, so adding their peaks would invent a moment that never
 * happened. The largest state reading is a number that was actually observed.
 */
export function groupByServer(states: readonly ConnectionStateCount[]): ServerConnections[] {
  const byServer = new Map<string, ConnectionStateCount[]>();
  for (const state of states) {
    const label = formatServer(state.server);
    const existing = byServer.get(label);
    if (existing) existing.push(state);
    else byServer.set(label, [state]);
  }

  return [...byServer.entries()]
    .map(([label, rows]) => ({
      label,
      states: [...rows].sort((left, right) => right.latest_backends - left.latest_backends),
      latestTotal: sum(rows.map((row) => row.latest_backends)),
      peakOfAnyState: Math.max(...rows.map((row) => row.peak_backends)),
      latestReadingAt: rows
        .map((row) => row.latest_reading_at)
        .reduce((latest, at) => (at > latest ? at : latest)),
      holdsOpenTransactions: rows.some(
        (row) => holdsAnOpenTransaction(row.state) && row.latest_backends > 0,
      ),
    }))
    .sort(
      (left, right) =>
        right.latestTotal - left.latestTotal || left.label.localeCompare(right.label),
    );
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

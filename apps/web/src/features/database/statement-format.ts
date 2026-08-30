/**
 * The statement list's numbers, written the way they are read.
 *
 * The rule that matters here: a figure derived from two readings is unknown until two
 * readings exist. `calls_in_window` is `max(calls) - min(calls)`, so a single poll makes
 * it zero — and "0" in a calls column reads as "this statement was not called", when the
 * truth is "we have only looked once". That distinction is the whole screen.
 */

import type { DatabaseServer, StatementCost } from "./api";

/** Fixed locale: the digit grouping must not change with the reader's machine. */
const NUMBER_LOCALE = "en-US";

/** Nothing measured. A rule, never a zero. */
const UNKNOWN = "—";

/** A difference across a window needs a start and an end to be a difference. */
const READINGS_FOR_A_DIFFERENCE = 2;

/** `queryid` is a 64-bit integer as text — too long to scan, long enough to confuse. */
const HASH_PREFIX_LENGTH = 12;

export function formatCount(count: number): string {
  if (!Number.isFinite(count) || count < 0) return UNKNOWN;
  return Math.round(count).toLocaleString(NUMBER_LOCALE);
}

/**
 * Calls during the window, or a rule when one poll cannot yet tell.
 *
 * `pg_stat_statements.calls` is cumulative since the last reset, so the answer is a
 * difference between two polls. With fewer than two there is no difference to report.
 */
export function formatCallsInWindow(statement: StatementCost): string {
  if (statement.readings < READINGS_FOR_A_DIFFERENCE) return UNKNOWN;
  return formatCount(statement.calls_in_window);
}

/** Why the calls column is empty, said in the cell's tooltip rather than guessed at. */
export function describeCallsInWindow(statement: StatementCost): string {
  if (statement.readings < READINGS_FOR_A_DIFFERENCE) {
    return `Polled once in this window. Calls during a window are the rise between two polls, so there is nothing to subtract yet. ${formatCount(
      statement.calls_total,
    )} calls since the last pg_stat_statements_reset().`;
  }
  return `${formatCount(statement.calls_in_window)} of ${formatCount(
    statement.calls_total,
  )} calls since the last pg_stat_statements_reset() happened in this window, across ${formatCount(
    statement.readings,
  )} polls.`;
}

/** Address and port, or just the address when the poller reported no port. */
export function formatServer(server: DatabaseServer): string {
  if (!server.address) return UNKNOWN;
  return server.port > 0 ? `${server.address}:${server.port}` : server.address;
}

/** Enough of the hash to tell two statements apart in a column. */
export function shortHash(queryHash: string): string {
  if (!queryHash) return UNKNOWN;
  return queryHash.length > HASH_PREFIX_LENGTH
    ? `${queryHash.slice(0, HASH_PREFIX_LENGTH)}…`
    : queryHash;
}

/**
 * How a person turns a hash back into a query.
 *
 * We hold no statement text by design, so the screen's job is to hand over the exact
 * lookup rather than to apologise for the gap.
 */
export function describeHashLookup(statement: StatementCost): string {
  const database = statement.namespace || "your database";
  return `queryid ${statement.query_hash}. Run: SELECT query FROM pg_stat_statements WHERE queryid = ${statement.query_hash}; against ${database} on ${formatServer(
    statement.server,
  )} to see the text — we never store it.`;
}

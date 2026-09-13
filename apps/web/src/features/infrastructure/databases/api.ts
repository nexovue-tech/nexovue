import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { filterCacheKey } from "@/shared/filters";
import type { TimeWindow } from "@/shared/selection";

/**
 * What door 4b's Postgres poller read. Field names are the API's, unchanged.
 *
 * Two reads, because they answer different questions and fail independently. The
 * statement list is what `pg_stat_statements` reported; the connection list is what
 * `pg_stat_activity` reported. A poll can produce the second and not the first — the
 * extension is optional, and a freshly reset one holds nothing — so "no database is
 * connected" and "connected, and it has no statements to show" are different screens.
 *
 * There is no statement text anywhere in here, on purpose: `pg_stat_statements` does not
 * reliably strip literals, so the poller sends `queryid` and never `query`.
 */

/** Which server a reading came from. Address and port, never a DSN. */
export interface DatabaseServer {
  address: string;
  port: number;
}

export interface StatementCost {
  /** `pg_stat_statements.queryid`, as text. The only identity a statement has here. */
  query_hash: string;
  /** The database the statement ran in, from `db.namespace`. */
  namespace: string;
  server: DatabaseServer;
  db_system: string;
  latest_mean_ms: number;
  worst_mean_ms: number;
  /** Cumulative since the last `pg_stat_statements_reset()`. */
  calls_total: number;
  /** The rise across the window. A reset inside it makes this a floor, not a count. */
  calls_in_window: number;
  readings: number;
  latest_reading_at: string;
}

export interface ConnectionStateCount {
  /** A `pg_stat_activity` backend state: active, idle, idle in transaction, … */
  state: string;
  server: DatabaseServer;
  latest_backends: number;
  peak_backends: number;
  readings: number;
  latest_reading_at: string;
}

interface SlowStatementsResponse {
  statements: StatementCost[];
}

interface ConnectionHealthResponse {
  states: ConnectionStateCount[];
  /** The latest readings summed across every state and server returned. */
  latest_backends_total: number;
}

/**
 * The poller sends its hundred costliest statements per database, so this holds several
 * connected databases whole rather than truncating one. The API's own ceiling is 500.
 */
const STATEMENT_LIMIT = 200;

export const slowStatementsQueryKey = (
  projectId: string,
  timeWindow: TimeWindow,
  narrowing: string,
) => ["dbstats", "statements", projectId, timeWindow.from, timeWindow.to, narrowing] as const;

export const connectionHealthQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["dbstats", "connections", projectId, timeWindow.from, timeWindow.to] as const;

export function useSlowStatements(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  narrowing: Record<string, string> = {},
) {
  return useQuery({
    queryKey: slowStatementsQueryKey(projectId ?? "", timeWindow, filterCacheKey(narrowing)),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<SlowStatementsResponse>("dbstats/statements", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          limit: STATEMENT_LIMIT,
          ...narrowing,
        },
        signal,
      }),
    select: (response) => response.statements,
  });
}

export function useConnectionHealth(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: connectionHealthQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ConnectionHealthResponse>("dbstats/connections", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

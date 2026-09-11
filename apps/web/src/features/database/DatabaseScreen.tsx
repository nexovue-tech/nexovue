import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatDuration } from "@/shared/format/duration";
import { formatCount } from "@/shared/format/number";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type { ConnectionStateCount, StatementCost } from "./api";
import { useConnectionHealth, useSlowStatements } from "./api";
import type { ServerConnections } from "./connection-health";
import { describeState, groupByServer, holdsAnOpenTransaction } from "./connection-health";
import {
  describeCallsInWindow,
  describeHashLookup,
  formatCallsInWindow,
  formatServer,
  shortHash,
} from "./statement-format";

/**
 * What the door 4b poller read out of a customer's Postgres.
 *
 * Two reads that fail and empty independently. `pg_stat_activity` needs no extension, so
 * connection health arrives from the first successful poll; `pg_stat_statements` is
 * optional and can be freshly reset, so the statement list can be empty while the
 * database is plainly connected. Those two states need different sentences, which is why
 * the screen asks both questions rather than one.
 *
 * No chart. The endpoints return one summary row per statement and per state, not a
 * series — so there is no measured shape over time to draw, and drawing one anyway would
 * be a picture of nothing.
 */

const OVERSCAN_ROWS = 8;
const ROW_HEIGHT = 40;

/* Column widths, declared once so the header and every row cannot drift apart. */
const MEAN_COLUMN = "w-20 shrink-0 text-right";
const WORST_COLUMN = "w-20 shrink-0 text-right";
const CALLS_COLUMN = "w-24 shrink-0 text-right";
const TOTAL_COLUMN = "hidden w-24 shrink-0 text-right lg:block";
const SEEN_COLUMN = "hidden w-28 shrink-0 text-right md:block";

export function DatabaseScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow(rangeId);

  const statements = useSlowStatements(projectId, timeWindow);
  const connections = useConnectionHealth(projectId, timeWindow);

  const isPending = statements.isPending || connections.isPending;
  const isError = statements.isError || connections.isError;

  const servers = groupByServer(connections.data?.states ?? []);
  const rows = statements.data ?? [];
  const polled = servers.length > 0 || rows.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Database</h1>
        <p className="mt-1 text-ink-dim text-sm">
          What we read from your Postgres in the last {timeRangeById(rangeId).label}: which
          statements cost the most time, and how many backends are connected.
        </p>
      </header>

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={8} label="Loading database statistics" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure
            title="Database statistics could not be loaded"
            error={statements.error ?? connections.error}
            onRetry={() => {
              if (statements.isError) void statements.refetch();
              if (connections.isError) void connections.refetch();
            }}
          />
        </div>
      ) : !polled ? (
        <NothingPolled />
      ) : (
        <>
          <ConnectionHealth servers={servers} />
          {rows.length === 0 ? <NoStatements /> : <StatementTable statements={rows} />}
        </>
      )}
    </div>
  );
}

/**
 * The empty state that matters, and every clause in it is checked against the code that
 * would fill the screen.
 *
 * `pollers/tasks.py` polls a row of `integrations`; `integrations/router.py` is how one
 * is created; `postgres_connect.py` refuses every auth mode except RDS IAM and says so
 * in its own error, because this system stores no customer database password by
 * design. Telling someone to paste a connection string would be telling them to do
 * something that cannot work.
 */
function NothingPolled() {
  return (
    <EmptyState
      title="No database has been polled yet"
      detail="Nothing here is empty because your database is quiet — we have not been given one to read. Connect a Postgres integration (POST /api/v1/integrations with kind postgres, naming the host, database, user and the IAM role to assume) and the scheduler starts polling it on the next interval. It has to be an RDS instance with IAM database authentication enabled: we mint a short-lived token per poll rather than storing a password, so there is no other way in."
    />
  );
}

/**
 * Connected, and still no statements. A real and different situation: the connection
 * read needs no extension, the statement read needs one.
 */
function NoStatements() {
  return (
    <EmptyState
      title="Connected, but no statements were reported"
      detail="The poller reached your database and read its connections, so the credentials and the network path are fine. pg_stat_statements returned nothing: either the extension is not installed in this database, or its counters were reset and nothing has run since. Add it to shared_preload_libraries and CREATE EXTENSION pg_stat_statements, then wait one poll."
    />
  );
}

function ConnectionHealth({ servers }: { servers: readonly ServerConnections[] }) {
  if (servers.length === 0) return null;

  return (
    <section className="shrink-0 border-line border-b px-4 pb-4">
      <h2 className="pb-2 font-mono text-2xs text-ink-dim uppercase tracking-caps">Connections</h2>
      <div className="flex flex-wrap gap-3">
        {servers.map((server) => (
          <ServerCard key={server.label} server={server} />
        ))}
      </div>
    </section>
  );
}

function ServerCard({ server }: { server: ServerConnections }) {
  return (
    <article className="min-w-64 flex-1 border-line border-l-2 bg-surface px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate font-mono text-ink text-xs">{server.label}</span>
        <span
          className="shrink-0 font-mono text-2xs text-ink-dim"
          title={`Last read ${formatTimestamp(server.latestReadingAt)}`}
        >
          {formatRelativeTime(server.latestReadingAt)}
        </span>
      </div>

      <p className="mt-1 text-ink text-sm">
        <span className="font-mono">{formatCount(server.latestTotal)}</span>
        <span className="text-ink-dim"> backends now</span>
        <span
          className="text-ink-dim"
          title="The largest single state reading in this window. Not a peak total: the states were not sampled at the same instant, so adding their peaks would describe a moment that never happened."
        >
          {" · peak "}
          <span className="font-mono">{formatCount(server.peakOfAnyState)}</span>
          {" in one state"}
        </span>
      </p>

      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {server.states.map((state) => (
          <StateCount key={state.state} state={state} />
        ))}
      </ul>
    </article>
  );
}

/**
 * Health is the absence of colour. The one state that earns it is a backend idle inside
 * an open transaction — that holds locks and holds back vacuum whatever the count is,
 * so it needs no invented threshold to be worth marking.
 */
function StateCount({ state }: { state: ConnectionStateCount }) {
  const held = holdsAnOpenTransaction(state.state) && state.latest_backends > 0;

  return (
    <li className="flex items-baseline gap-1.5" title={describeState(state.state)}>
      <span className={`font-mono text-2xs ${held ? "text-warning-text" : "text-ink"}`}>
        {formatCount(state.latest_backends)}
      </span>
      <span className={`text-2xs ${held ? "text-warning-text" : "text-ink-dim"}`}>
        {state.state}
      </span>
    </li>
  );
}

function StatementTable({ statements }: { statements: readonly StatementCost[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: statements.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center border-line border-b bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="min-w-0 flex-1">Statement</span>
        <span className={MEAN_COLUMN}>Mean</span>
        <span className={WORST_COLUMN}>Worst</span>
        <span className={CALLS_COLUMN}>Calls</span>
        <span className={TOTAL_COLUMN}>Calls total</span>
        <span className={SEEN_COLUMN}>Last read</span>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
          {virtualizer.getVirtualItems().map((item) => (
            <div
              key={statements[item.index].query_hash + statements[item.index].namespace}
              className="absolute top-0 left-0 w-full"
              style={{ height: `${item.size}px`, transform: `translateY(${item.start}px)` }}
            >
              <StatementRow statement={statements[item.index]} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StatementRow({ statement }: { statement: StatementCost }) {
  return (
    <div className="flex h-full items-center border-line-subtle border-b px-4 text-sm">
      <span className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="truncate font-mono text-ink text-xs" title={describeHashLookup(statement)}>
          {shortHash(statement.query_hash)}
        </span>
        <span className="truncate text-2xs text-ink-dim" title={formatServer(statement.server)}>
          {statement.namespace}
        </span>
      </span>

      <span className={`${MEAN_COLUMN} font-mono text-ink text-2xs`}>
        {formatDuration(statement.latest_mean_ms)}
      </span>

      <span
        className={`${WORST_COLUMN} font-mono text-2xs text-ink-dim`}
        title="The highest mean execution time seen across the polls in this window."
      >
        {formatDuration(statement.worst_mean_ms)}
      </span>

      <span
        className={`${CALLS_COLUMN} font-mono text-2xs text-ink-dim`}
        title={describeCallsInWindow(statement)}
      >
        {formatCallsInWindow(statement)}
      </span>

      <span
        className={`${TOTAL_COLUMN} font-mono text-2xs text-ink-dim`}
        title="Calls since the last pg_stat_statements_reset(), which is the counter's own zero."
      >
        {formatCount(statement.calls_total)}
      </span>

      <span
        className={`${SEEN_COLUMN} font-mono text-2xs text-ink-dim`}
        title={formatTimestamp(statement.latest_reading_at)}
      >
        {formatRelativeTime(statement.latest_reading_at)}
      </span>
    </div>
  );
}

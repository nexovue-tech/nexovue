import { useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { appliedFilters, FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { filterParams, parseFilterValues, withFilter } from "@/shared/filters";
import { formatBinaryBytes } from "@/shared/format/bytes";
import { formatCount, formatPercent, UNKNOWN } from "@/shared/format/number";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";
import type { NodeSummary, NodeTrendPoint, SignalSummary } from "./api";
import { nodeSeriesKey, useNodes, useNodeTrend, useSignals } from "./api";
import { DatabasesView } from "./databases/DatabasesView";
import { infrastructureFilters } from "./filters";
import {
  CPU_METRICS,
  cpuFraction,
  DISK_METRICS,
  describeMemory,
  describeNetworkFaults,
  describeSaturation,
  describeStale,
  describeUnattributed,
  explainMissing,
  explainStale,
  explainUnattributed,
  FILESYSTEM_METRICS,
  formatByteRate,
  formatCpu,
  isUnattributed,
  NETWORK_METRICS,
  nodeName,
  STOPPED_REPORTING,
} from "./node-readings";

/**
 * The hosts and containers behind the services: CPU, memory, disk and network.
 *
 * A third kind of row appears when a resource named no machine at all: the readings are
 * grouped by the process that sent them and labelled "reported by …", never by a host we
 * were never told about. `UnattributedNotice` says so once, with the one-line fix.
 *
 * A node that has gone quiet is marked and counted rather than dropped or reordered.
 * "Stopped reporting" is the whole claim: a machine that was switched off and a collector
 * that crashed are indistinguishable from here, and the API's `stale` says only that the
 * node fell silent for several of its own reporting intervals.
 *
 * Kubernetes metrics are ingested and queryable, but pod, node and namespace views are
 * Phase 2 and deliberately absent — a fleet list is not the place to invent a cluster
 * model.
 *
 * No chart. `/infrastructure/hosts` returns one summary row per node, not a series, so
 * there is no measured shape over time to draw. The utilization bars below are a
 * rendering of a single measured fraction and nothing more; they are omitted entirely
 * when that fraction was not reported, rather than drawn empty.
 */

const OVERSCAN_ROWS = 10;
const ROW_HEIGHT = 44;

/* Column widths, declared once so the header and every row cannot drift apart. */
const CPU_COLUMN = "w-28 shrink-0 text-right";
/* 104px of sparkline plus its 12px gutter, reserved on every row. */
const TREND_COLUMN = "w-29 shrink-0 pl-3";
const MEMORY_COLUMN = "w-32 shrink-0 text-right";
const STORAGE_COLUMN = "hidden w-32 shrink-0 text-right lg:block";
const DISK_COLUMN = "hidden w-28 shrink-0 text-right md:block";
const NETWORK_COLUMN = "hidden w-28 shrink-0 text-right md:block";
const SEEN_COLUMN = "hidden w-24 shrink-0 text-right lg:block";

/**
 * Hosts and databases are one destination, not two.
 *
 * Both are polled rather than pushed, both are thin on their own, and the flows found
 * that a database is reached *from a trace* far more often than it is browsed — so it
 * belongs where a reader already is rather than behind its own sidebar entry (IA2).
 */
const VIEWS = [
  { id: "hosts", label: "Hosts" },
  { id: "databases", label: "Databases" },
] as const;

type InfrastructureView = (typeof VIEWS)[number]["id"];

function ViewSwitch({
  view,
  onSelect,
}: {
  view: InfrastructureView;
  onSelect: (view: InfrastructureView) => void;
}) {
  return (
    <div className="flex shrink-0 items-baseline gap-1.5 font-mono text-2xs uppercase tracking-caps">
      {VIEWS.map((option) => (
        <button
          key={option.id}
          type="button"
          aria-pressed={view === option.id}
          onClick={() => onSelect(option.id)}
          className={`rounded-sm border px-1.5 py-0.5 ${
            view === option.id
              ? "border-line bg-raised text-ink"
              : "border-transparent text-ink-dim hover:text-ink"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function InfrastructureScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();
  const navigate = useNavigate();

  // The route's own id, not its path: `/infrastructure` renders inside the shell.
  const search = useSearch({ from: "/shell/infrastructure" });

  const [view, setView] = useState<InfrastructureView>("hosts");

  // The options depend on what came back, and what came back depends on the
  // filters — so the definitions are rebuilt from each answer. `FilterBar` keeps
  // a selected value that is no longer offered, which is what stops the control
  // and the results disagreeing while a filter is applied.
  const [definitions, setDefinitions] = useState(() => infrastructureFilters([]));
  const filters = useMemo(() => parseFilterValues(definitions, search), [definitions, search]);
  const narrowing = useMemo(() => filterParams(filters), [filters]);

  const nodes = useNodes(projectId, timeWindow, narrowing);
  const trendBySeries = useNodeTrend(projectId, timeWindow);
  const signals = useSignals(projectId, timeWindow);

  const rows = nodes.data?.nodes ?? [];
  useEffect(() => {
    if (rows.length > 0) setDefinitions(infrastructureFilters(rows));
  }, [rows]);

  // `replace`, not push: the back button belongs to the screen a person came
  // from, not to every control they tried on the way.
  const show = (next: Record<string, string | undefined>) =>
    void navigate({ to: "/infrastructure", search: () => filterParams(next), replace: true });

  const narrowed = appliedFilters(definitions, filters).length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <div className="flex items-baseline justify-between gap-6">
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
            Infrastructure
          </h1>
          <ViewSwitch view={view} onSelect={setView} />
        </div>
        {view === "hosts" ? (
          <p className="mt-1 text-ink-dim text-sm">
            The hosts, containers and reporting processes behind your services, over the last{" "}
            {windowPhrase}. A dash is a measurement nobody sent, never a zero.
          </p>
        ) : null}
      </header>

      {view === "databases" ? (
        <DatabasesView />
      ) : nodes.isPending || signals.isPending ? (
        <div className="px-4">
          <SkeletonRows rows={8} label="Loading hosts" />
        </div>
      ) : nodes.isError || signals.isError ? (
        <div className="px-4">
          <InlineFailure
            title="Hosts could not be loaded"
            error={nodes.error ?? signals.error}
            onRetry={() => {
              if (nodes.isError) void nodes.refetch();
              if (signals.isError) void signals.refetch();
            }}
          />
        </div>
      ) : (
        <>
          <FilterBar
            definitions={definitions}
            values={filters}
            onChange={(key, value) => show(withFilter(filters, key, value))}
            onClearAll={() => show({})}
          />
          {rows.length === 0 ? (
            <NothingReporting narrowed={narrowed} onClearFilters={() => show({})} />
          ) : (
            <>
              {nodes.data?.truncated ? <TruncationNotice /> : null}
              <NestingNotice nodes={rows} />
              <StaleNotice nodes={rows} />
              <SaturationNotice nodes={rows} />
              <NetworkFaultNotice nodes={rows} />
              <UnattributedNotice nodes={rows} />
              <NodeTable nodes={rows} trendBySeries={trendBySeries} />
              <SignalStrip signals={signals.data ?? []} />
            </>
          )}
        </>
      )}
    </div>
  );
}

/**
 * The empty state, and every clause in it is true of the code that would fill the screen.
 *
 * `infrastructure/conventions.py` lists the metric names read — `system.*` for a host,
 * `container.*` for a container — and those come from a collector the customer runs, not
 * from anything we deploy. There is no nexovue agent to install, so the instruction is
 * the receiver name and the endpoint, which is the whole of what they have to do.
 */
function NothingReporting({
  narrowed,
  onClearFilters,
}: {
  narrowed: boolean;
  onClearFilters: () => void;
}) {
  // "Nothing matches these filters" and "no collector is running" ask for
  // opposite actions, and one sentence for both sends half the readers to set up
  // a collector they already have.
  if (narrowed) {
    return (
      <EmptyState
        title="No nodes match these filters"
        detail="Your collector is reporting, but nothing in this window matches what you asked for. Drop a filter, or widen the time range."
        action={
          <Button variant="secondary" onClick={onClearFilters}>
            Clear all filters
          </Button>
        }
      />
    );
  }

  return (
    <EmptyState
      title="No host metrics have arrived"
      detail="Nothing is scraping your machines yet. Host and container metrics come from a collector you run — enable the hostmetrics receiver in your OpenTelemetry Collector (and docker_stats for containers), point its OTLP exporter at your ingest endpoint with your API key, and the fleet appears here on the next scrape. We have no agent to install: this screen shows what your collector sends and nothing else."
    />
  );
}

function TruncationNotice() {
  return (
    <p className="shrink-0 border-warning-border border-l-2 px-4 py-2 text-sm text-warning-text">
      This is the start of the fleet, not all of it — the read hit its series ceiling. Narrow the
      time range to see a complete list.
    </p>
  );
}

/**
 * The nodes we have stopped hearing from, named once above the table.
 *
 * A mark on the row alone is not enough: the list is virtualized, so a node that
 * vanished can sit below the fold, and that is the single most important thing this
 * screen has to say. The rows themselves stay exactly where they are — reordering by
 * staleness would rank a restart artefact against a real outage, and nothing here knows
 * which is which.
 */
function StaleNotice({ nodes }: { nodes: readonly NodeSummary[] }) {
  const said = describeStale(nodes);
  if (said === null) return null;

  return (
    <p className="shrink-0 border-critical-border border-l-2 px-4 py-2 text-critical-text text-sm">
      {said}
    </p>
  );
}

/**
 * USE's middle letter, said once above the table.
 *
 * Saturation is work that arrived and is waiting, and it is a different
 * question from utilization: a disk with operations queued behind it is slower
 * than its own busy-ness suggests, and a host swapping is out of memory
 * whatever its memory percentage says. Both readings come from the standard
 * `hostmetrics` receiver and were collected and drawn by nothing.
 */
function SaturationNotice({ nodes }: { nodes: readonly NodeSummary[] }) {
  const said = describeSaturation(nodes);
  if (said === null) return null;

  return (
    <p className="shrink-0 border-warning-border border-l-2 px-4 py-2 text-sm text-warning-text">
      {said}
    </p>
  );
}

/**
 * USE's last letter. A sentence rather than a column because it is almost
 * always zero, and a column of zeroes is rows of nothing.
 */
function NetworkFaultNotice({ nodes }: { nodes: readonly NodeSummary[] }) {
  const said = describeNetworkFaults(nodes);
  if (said === null) return null;

  return (
    <p className="shrink-0 border-critical-border border-l-2 px-4 py-2 text-critical-text text-sm">
      {said}
    </p>
  );
}

/**
 * Host metrics that cannot be attributed to a machine, and the line that fixes it.
 *
 * Said once for the fleet rather than once per row: it is one setting on one resource,
 * and a reader who acts on it never sees these rows again.
 */
function UnattributedNotice({ nodes }: { nodes: readonly NodeSummary[] }) {
  const said = describeUnattributed(nodes);
  if (said === null) return null;

  return (
    <p className="shrink-0 border-warning-border border-l-2 px-4 py-2 text-sm text-warning-text">
      {said}
    </p>
  );
}

/**
 * Says once what the rows cannot say individually: a container's usage is already
 * inside its host's.
 *
 * There is no total on this screen, so nothing is summed wrongly today — but a reader
 * adding a host's memory to its containers' would double-count real bytes, and the
 * list gives them no reason not to. Shown only when both kinds are present.
 */
function NestingNotice({ nodes }: { nodes: readonly NodeSummary[] }) {
  const hasHost = nodes.some((node) => node.kind === "host");
  const hasContainer = nodes.some((node) => node.kind === "container");
  if (!hasHost || !hasContainer) return null;

  return (
    <p className="shrink-0 px-4 pb-2 text-ink-dim text-xs">
      A container&rsquo;s usage is already counted inside its host&rsquo;s. These are two views of
      the same machine, not two machines, so their rows should not be added together.
    </p>
  );
}

/**
 * Memory, not CPU — decided by measuring the real stack rather than by assuming.
 *
 * CPU looked like the obvious column and is the wrong one. Docker reports
 * `container.cpu.utilization` as a **cumulative** figure, not an instantaneous rate:
 * `accounting` moves from 0.113 to 0.118 across thirty-six minutes, and `flagd` from
 * 128.3 to 131.5. Measured across the dev stack the median CPU series swings 4.4% of its
 * own range, so a sparkline of it is a flat ramp at every scale — and one node reporting
 * a thousand times another flattens the rest besides.
 *
 * Memory swings 40.5%. It is the measure on this screen that actually carries a shape.
 *
 * **The ceiling is the node's own limit, which is a physical bound rather than a
 * per-row autoscale.** Half of your limit is half of your limit whatever the limit is,
 * so two rows remain comparable — unlike a maximum derived from whichever row happens to
 * be busiest, which is the autoscaling the archetype refuses.
 */
const MEMORY_USAGE_METRICS = ["container.memory.usage.total", "system.memory.usage"] as const;

function memorySeries(
  node: NodeSummary,
  trendBySeries: ReadonlyMap<string, readonly NodeTrendPoint[]> | undefined,
): { points: readonly NodeTrendPoint[]; ceiling: number } | undefined {
  if (node.memory_limit_bytes === null || node.memory_limit_bytes <= 0) return undefined;

  const key = nodeSeriesKey(node);
  for (const metric of MEMORY_USAGE_METRICS) {
    const points = trendBySeries?.get(`${key}|${metric}`);
    if (points) return { points, ceiling: node.memory_limit_bytes };
  }
  return undefined;
}

function NodeTable({
  nodes,
  trendBySeries,
}: {
  nodes: readonly NodeSummary[];
  trendBySeries?: ReadonlyMap<string, readonly NodeTrendPoint[]>;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: nodes.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center border-line border-b bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="min-w-0 flex-1">Node</span>
        <span className={CPU_COLUMN}>CPU</span>
        <span className={MEMORY_COLUMN}>Memory</span>
        <span className={STORAGE_COLUMN}>Disk used</span>
        <span className={DISK_COLUMN}>Disk I/O</span>
        <span className={NETWORK_COLUMN}>Network</span>
        <span className={SEEN_COLUMN}>Last seen</span>
        <span className={TREND_COLUMN}>Memory trend</span>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
          {virtualizer.getVirtualItems().map((item) => (
            <div
              key={nodeKey(nodes[item.index])}
              className="absolute top-0 left-0 w-full"
              style={{ height: `${item.size}px`, transform: `translateY(${item.start}px)` }}
            >
              <NodeRow node={nodes[item.index]} trendBySeries={trendBySeries} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Every field the API groups a node by, so two reporter rows cannot share a key. */
function nodeKey(node: NodeSummary): string {
  return `${node.host}/${node.container}/${node.reporter}/${node.reporter_instance}`;
}

function NodeRow({
  node,
  trendBySeries,
}: {
  node: NodeSummary;
  trendBySeries?: ReadonlyMap<string, readonly NodeTrendPoint[]>;
}) {
  const memory = memorySeries(node, trendBySeries);

  return (
    <div className="flex h-full items-center border-line-subtle border-b px-4 text-sm">
      <span className="flex min-w-0 flex-1 items-baseline gap-2">
        <span
          className={`truncate ${isUnattributed(node) ? "text-ink-dim italic" : "text-ink"}`}
          title={isUnattributed(node) ? explainUnattributed(node) : undefined}
        >
          {nodeName(node)}
        </span>
        <span className="shrink-0 font-mono text-2xs text-ink-dim">{node.kind}</span>
        {node.stale ? <StoppedReportingBadge node={node} /> : null}
        {node.container && node.host ? (
          <span className="hidden shrink-0 truncate font-mono text-2xs text-ink-dim lg:inline">
            on {node.host}
          </span>
        ) : null}
        {node.reporter_instance ? (
          <span
            className="hidden shrink-0 truncate font-mono text-2xs text-ink-dim lg:inline"
            title={`service.instance.id ${node.reporter_instance}`}
          >
            instance {node.reporter_instance}
          </span>
        ) : null}
      </span>

      <span className={CPU_COLUMN}>
        <Measurement
          value={formatCpu(node)}
          missingReason={explainMissing(node, CPU_METRICS)}
          fraction={cpuFraction(node)}
        />
      </span>

      <span className={MEMORY_COLUMN}>
        <Measurement
          value={memoryCell(node)}
          missingReason={describeMemory(node)}
          title={describeMemory(node)}
          fraction={node.memory_utilization}
        />
      </span>

      <span className={STORAGE_COLUMN}>
        <Measurement
          value={storageCell(node)}
          missingReason={describeStorage(node)}
          title={describeStorage(node)}
          fraction={node.filesystem_utilization}
        />
      </span>

      <span className={`${DISK_COLUMN} font-mono text-2xs`}>
        <Plain
          value={formatByteRate(node.disk_bytes_per_second)}
          missingReason={explainMissing(node, DISK_METRICS)}
        />
      </span>

      <span className={`${NETWORK_COLUMN} font-mono text-2xs`}>
        <Plain
          value={formatByteRate(node.network_bytes_per_second)}
          missingReason={explainMissing(node, NETWORK_METRICS)}
        />
      </span>

      <span
        className={`${SEEN_COLUMN} font-mono text-2xs text-ink-dim`}
        title={`First seen ${formatTimestamp(node.first_seen)} · last seen ${formatTimestamp(node.last_seen)}`}
      >
        {formatRelativeTime(node.last_seen)}
      </span>

      <span className={TREND_COLUMN}>
        <Sparkline
          points={(memory?.points ?? []).map((point) => point.value)}
          max={memory?.ceiling ?? 0}
          tone={node.stale ? "critical" : "series"}
          label={`${nodeName(node)} memory against its limit, over the window`}
        />
      </span>
    </div>
  );
}

/**
 * The mark that separates a node still reporting from one that has gone quiet — the two
 * were rendered identically, which is what made a dead row indistinguishable from a live
 * one.
 *
 * It says "stopped reporting" rather than "down" because those are different claims and
 * the telemetry only supports the first. The tooltip carries the evidence: how long the
 * silence is, and the cadence it broke.
 */
function StoppedReportingBadge({ node }: { node: NodeSummary }) {
  return (
    <span
      className="shrink-0 rounded-sm border border-critical-border bg-critical-subtle px-1.5 py-0.5 font-mono text-2xs text-critical-text uppercase tracking-caps"
      title={explainStale(node)}
    >
      {STOPPED_REPORTING}
    </span>
  );
}

function memoryCell(node: NodeSummary): string {
  if (node.memory_utilization !== null) return formatPercent(node.memory_utilization);
  if (node.memory_used_bytes !== null) return formatBinaryBytes(node.memory_used_bytes);
  return UNKNOWN;
}

/**
 * Filesystem space, summed across every volume the node reported. Separate from the
 * disk I/O column beside it: a full disk and a busy disk are different incidents.
 */
function storageCell(node: NodeSummary): string {
  if (node.filesystem_utilization !== null) return formatPercent(node.filesystem_utilization);
  if (node.filesystem_used_bytes !== null) return formatBinaryBytes(node.filesystem_used_bytes);
  return UNKNOWN;
}

function describeStorage(node: NodeSummary): string {
  if (node.filesystem_used_bytes === null && node.filesystem_limit_bytes === null) {
    return explainMissing(node, FILESYSTEM_METRICS);
  }
  return `${formatBinaryBytes(node.filesystem_used_bytes)} used of ${formatBinaryBytes(
    node.filesystem_limit_bytes,
  )}, summed across every mounted filesystem this node reported.`;
}

/**
 * A measured fraction, with the bar underneath it.
 *
 * The bar is drawn only when there is a fraction to draw. An empty track next to a dash
 * would read as "measured, and at zero" — the one thing this screen must never say.
 */
function Measurement({
  value,
  missingReason,
  title,
  fraction,
}: {
  value: string;
  missingReason: string;
  title?: string;
  fraction: number | null;
}) {
  if (value === UNKNOWN) return <Plain value={value} missingReason={missingReason} />;

  return (
    <span className="flex flex-col items-end gap-1" title={title}>
      <span className="font-mono text-ink text-2xs">{value}</span>
      {fraction === null ? null : <UtilizationBar fraction={fraction} />}
    </span>
  );
}

/** One measured fraction, drawn. Never a placeholder, never an estimate. */
function UtilizationBar({ fraction }: { fraction: number }) {
  const filled = Math.min(Math.max(fraction, 0), 1) * 100;

  return (
    <span aria-hidden="true" className="block h-0.5 w-16 bg-raised">
      <span className="block h-full bg-brand" style={{ width: `${filled}%` }} />
    </span>
  );
}

/** A plain cell whose dash explains itself. */
function Plain({ value, missingReason }: { value: string; missingReason: string }) {
  if (value === UNKNOWN) {
    return (
      <span className="font-mono text-2xs text-ink-dim" title={missingReason}>
        {UNKNOWN}
      </span>
    );
  }
  return <span className="font-mono text-2xs text-ink">{value}</span>;
}

/**
 * Which metrics arrived, under the fleet.
 *
 * The node list says what the fleet looks like; this says which scrapers are switched
 * on. A fleet showing CPU and dashes everywhere else is a collector with one receiver
 * enabled, and this strip is what makes that visible rather than mysterious.
 */
function SignalStrip({ signals }: { signals: readonly SignalSummary[] }) {
  if (signals.length === 0) return null;

  return (
    <section className="shrink-0 border-line border-t px-4 py-3">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Signals received</h2>
      <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5">
        {signals.map((signal) => (
          <li
            key={signal.metric_name}
            className="flex items-baseline gap-1.5"
            title={`${formatCount(signal.points)} points across ${formatCount(signal.series)} series from ${formatCount(signal.nodes)} node(s). Last ${formatTimestamp(signal.last_seen)}.`}
          >
            <span className="font-mono text-2xs text-ink">{signal.metric_name}</span>
            <span className="font-mono text-2xs text-ink-dim">{formatCount(signal.points)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

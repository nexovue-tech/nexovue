import { Link } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { DurationHeatmap } from "@/shared/components/DurationHeatmap";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { CHART_HEIGHT, TrendChart } from "@/shared/components/TrendChart";
import type { DeployMarker } from "@/shared/deploy-markers";
import { deployMarkers } from "@/shared/deploy-markers";
import { formatDuration } from "@/shared/format/duration";
import { formatErrors } from "@/shared/format/health";
import { formatCount, formatPercent } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";
import { formatRelativeTime } from "@/shared/format/time";
import { SCOPED_PATHS, scopedSearch } from "@/shared/scope";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";
import type { HostGroup, ServiceEdge } from "@/shared/service-peers";
import { collapseAddressPeers, EDGE_QUEUE, isPlaceholderService } from "@/shared/service-peers";

import type {
  DurationHeatmapResponse,
  EndpointHealth,
  ErrorGroupSummary,
  HealthMetrics,
  ServiceHealth,
  TrendPoint,
} from "./api";
import {
  useDeploys,
  useDurationHeatmap,
  useService,
  useServiceDependencies,
  useServiceEndpoints,
  useServiceErrors,
  useServiceTrend,
} from "./api";

import type { Dependency, DetailState } from "./service-detail";
import {
  dependencyKey,
  describeEndpoints,
  describeService,
  describeServiceErrors,
  describeSilence,
  detailState,
  neighboursOf,
} from "./service-detail";

import type { TrendMetric, TrendPlot } from "./trend-series";
import { buildTrendPlots, describePlot } from "./trend-series";

const ENDPOINT_ROW_HEIGHT = 32;
const OVERSCAN_ROWS = 10;

const PERCENT = 100;

/* Column widths, declared once so the header and every row cannot drift apart. */
const RATE_COLUMN = "w-20 shrink-0 text-right";
const ERRORS_COLUMN = "w-28 shrink-0 text-right";
const LATENCY_COLUMN = "w-16 shrink-0 text-right";
const WIDE_ONLY = "hidden lg:block";

/** Each chart's y axis reads in its own unit; a bare number would say nothing. */
const AXIS_FORMAT: Record<TrendMetric, (value: number) => string> = {
  rate: formatRatePerMinute,
  // `buildTrendPlots` has already multiplied out; the formatter wants the fraction back.
  errorRate: (percent) => formatPercent(percent / PERCENT),
  latency: formatDuration,
};

/**
 * One service: what it is doing now, what it did across the window, which endpoints
 * are dragging, and who it depends on.
 *
 * Where the service map and the overview both point. A name in the URL is a
 * caller-supplied value, and it goes to the API as a bound parameter — it narrows a
 * read this tenant is already entitled to and can never widen one.
 *
 * The route component owns the selections and the queries; everything visible is in
 * `ServiceDetailView`, which is a function of its props and nothing else.
 */
export function ServiceDetailScreen({ serviceName }: { serviceName: string }) {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow();

  const aggregate = useService(projectId, serviceName, timeWindow);
  const trend = useServiceTrend(projectId, serviceName, timeWindow);
  const endpoints = useServiceEndpoints(projectId, serviceName, timeWindow);
  const dependencies = useServiceDependencies(projectId, timeWindow);
  const errors = useServiceErrors(projectId, serviceName, timeWindow);
  const deploys = useDeploys(projectId);
  // Conditional (OD7): absent for a customer whose SDK sends no OTLP histogram,
  // which is most of them. `available` is the answer, not an empty array.
  const heatmap = useDurationHeatmap(projectId, serviceName, timeWindow);

  // One read for the project, narrowed to this service and this window. A
  // deploy of another service is an invitation to blame the wrong change for
  // the step in this chart.
  const markers = useMemo(
    () =>
      deployMarkers(deploys.data ?? [], {
        serviceName,
        from: timeWindow.from,
        to: timeWindow.to,
      }),
    [deploys.data, serviceName, timeWindow.from, timeWindow.to],
  );

  const points = trend.data?.points ?? [];
  const endpointRows = endpoints.data?.endpoints ?? [];
  const row = aggregate.data;

  const state = detailState({
    isPending: aggregate.isPending || trend.isPending || endpoints.isPending,
    isError: aggregate.isError || trend.isError || endpoints.isError,
    hasTrend: points.length > 0,
    hasEndpoints: endpointRows.length > 0,
    hasAggregate: row !== undefined,
  });

  return (
    <ServiceDetailView
      serviceName={serviceName}
      windowLabel={timeRangeById(rangeId).label}
      state={state}
      error={aggregate.error ?? trend.error ?? endpoints.error}
      onRetry={() => {
        void aggregate.refetch();
        void trend.refetch();
        void endpoints.refetch();
      }}
      row={row}
      points={points}
      markers={markers}
      endpoints={endpointRows}
      heatmap={heatmap.data}
      dependencies={{
        status: panelStatus(dependencies),
        data: dependencies.data?.edges ?? [],
        error: dependencies.error,
        onRetry: () => void dependencies.refetch(),
      }}
      errors={{
        status: panelStatus(errors),
        data: errors.data?.groups ?? [],
        error: errors.error,
        onRetry: () => void errors.refetch(),
      }}
    />
  );
}

/**
 * A panel that loads on its own clock. Its failure is not the screen's: losing the
 * dependencies or the errors must not blank the numbers a person came for.
 */
export interface PanelSource<T> {
  status: "pending" | "error" | "success";
  data: readonly T[];
  error?: unknown;
  onRetry: () => void;
}

function panelStatus(query: {
  isPending: boolean;
  isError: boolean;
}): PanelSource<never>["status"] {
  if (query.isPending) return "pending";
  return query.isError ? "error" : "success";
}

export interface ServiceDetailViewProps {
  serviceName: string;
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  state: DetailState;
  error?: unknown;
  onRetry: () => void;
  row?: ServiceHealth;
  points: readonly TrendPoint[];
  /** The deploys of this service inside this window, as rules on the charts. */
  markers: readonly DeployMarker[];
  endpoints: readonly EndpointHealth[];
  /** Absent for a customer whose SDK sends no OTLP histogram, which is most. */
  heatmap?: DurationHeatmapResponse;
  dependencies: PanelSource<ServiceEdge>;
  errors: PanelSource<ErrorGroupSummary>;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function ServiceDetailView({
  serviceName,
  windowLabel,
  state,
  error,
  onRetry,
  row,
  points,
  markers,
  endpoints,
  heatmap,
  dependencies,
  errors,
}: ServiceDetailViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [listOffset, setListOffset] = useState(0);

  /**
   * The endpoint list scrolls with the page rather than in a box of its own, so the
   * virtualizer has to be told where in that page it starts.
   *
   * Measured from the content above it rather than on a dependency list: charts and a
   * two-column grid change height on every resize, and a stale offset puts every row in
   * the wrong place. Watching the content is the one thing that catches all of it.
   */
  const watchContentHeight = useCallback((content: HTMLDivElement | null) => {
    if (!content) return;

    const measure = () => setListOffset(listRef.current?.offsetTop ?? 0);
    measure();

    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  const virtualizer = useVirtualizer({
    count: endpoints.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ENDPOINT_ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    scrollMargin: listOffset,
    getItemKey: (index) => endpointKey(endpoints[index]),
  });

  const plots = useMemo(() => buildTrendPlots(points), [points]);
  // Address-named peers are folded before the lists are sliced, so this panel and the
  // service map agree about how many dependencies there are.
  const peers = useMemo(() => collapseAddressPeers(dependencies.data), [dependencies.data]);
  const neighbours = useMemo(
    () => neighboursOf(peers.edges, serviceName),
    [peers.edges, serviceName],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ServiceHeader serviceName={serviceName} windowLabel={windowLabel} state={state} row={row} />

      {state === "pending" ? (
        <div className="px-4">
          <SkeletonRows rows={10} label={`Loading ${serviceName}`} />
        </div>
      ) : state === "failed" ? (
        <div className="px-4">
          <InlineFailure
            title={`${serviceName} could not be loaded`}
            error={error}
            onRetry={onRetry}
          />
        </div>
      ) : state === "silent" ? (
        <EmptyState
          title="Nothing reported in this window"
          detail={describeSilence(serviceName, windowLabel)}
        />
      ) : (
        <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-y-auto">
          <div ref={watchContentHeight}>
            {row ? <HeadlineMetrics row={row} /> : null}

            <TrendSection
              plots={plots}
              serviceName={serviceName}
              bucketCount={points.length}
              markers={markers}
            />

            <HeatmapSection serviceName={serviceName} heatmap={heatmap} />

            <DependencySection
              serviceName={serviceName}
              source={dependencies}
              callers={neighbours.callers}
              callees={neighbours.callees}
              hostGroups={peers.groups}
            />

            <ErrorSection serviceName={serviceName} source={errors} />

            <SectionHeading title="Endpoints" caption={describeEndpoints(endpoints)} />

            {endpoints.length > 0 ? (
              <>
                <EndpointColumnHeader />
                <div ref={listRef} className="relative w-full">
                  <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
                    {virtualizer.getVirtualItems().map((item) => (
                      <div
                        key={item.key}
                        className="absolute inset-x-0 top-0"
                        style={{
                          height: item.size,
                          transform: `translateY(${item.start - listOffset}px)`,
                        }}
                      >
                        <EndpointLine endpoint={endpoints[item.index]} />
                      </div>
                    ))}
                  </div>
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function endpointKey(endpoint: EndpointHealth): string {
  return `${endpoint.span_kind}:${endpoint.endpoint}`;
}

/**
 * The name, the sentence that summarises it, and the two ways out of this screen into
 * the traces behind the numbers.
 */
function ServiceHeader({
  serviceName,
  windowLabel,
  state,
  row,
}: {
  serviceName: string;
  windowLabel: string;
  state: DetailState;
  row?: ServiceHealth;
}) {
  return (
    <header className="shrink-0 px-4 pt-5 pb-3">
      <Link
        to="/services"
        className="font-mono text-2xs text-ink-dim uppercase tracking-caps hover:text-ink"
      >
        Services
      </Link>

      <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h1 className="min-w-0 break-all font-semibold font-serif text-ink text-xl tracking-tight">
          {serviceName}
        </h1>
        {/* The service travels with the link (IA1). A person who has found the
            failing service should not have to type its name again on the next
            screen — and each destination spells the parameter differently, which
            `scopedSearch` is where it stops mattering. */}
        <nav className="flex shrink-0 flex-wrap items-baseline gap-3 text-sm">
          <Link
            to={SCOPED_PATHS.traces}
            search={scopedSearch("traces", serviceName)}
            className={SCOPED_LINK_CLASS}
          >
            Traces
          </Link>
          <Link
            to={SCOPED_PATHS.traces}
            search={{ ...scopedSearch("traces", serviceName), status: "ERROR" }}
            className={SCOPED_LINK_CLASS}
          >
            Failed traces
          </Link>
          <Link
            to={SCOPED_PATHS.logs}
            search={scopedSearch("logs", serviceName)}
            className={SCOPED_LINK_CLASS}
          >
            Logs
          </Link>
          <Link
            to={SCOPED_PATHS.errors}
            search={scopedSearch("errors", serviceName)}
            className={SCOPED_LINK_CLASS}
          >
            Errors
          </Link>
        </nav>
      </div>

      <p className="mt-1 text-ink-dim text-sm">
        {state === "ready"
          ? describeService(row, windowLabel)
          : `What ${serviceName} did over the last ${windowLabel}.`}
      </p>
    </header>
  );
}

/** The six numbers the overview row carries, given room to be read one at a time. */
function HeadlineMetrics({ row }: { row: ServiceHealth }) {
  return (
    <dl className="grid grid-cols-2 gap-px border-line border-y bg-line sm:grid-cols-3 lg:grid-cols-6">
      <Headline label="Req/min" value={formatRatePerMinute(row.requests_per_minute)} />
      <Headline label="Requests" value={formatCount(row.request_count)} />
      <Headline
        label="Errors"
        value={formatErrors(row.error_count, row.error_rate)}
        critical={row.error_count > 0}
      />
      <Headline label="p50" value={formatDuration(row.p50_ms)} />
      <Headline label="p95" value={formatDuration(row.p95_ms)} emphasis />
      <Headline label="p99" value={formatDuration(row.p99_ms)} />
    </dl>
  );
}

function Headline({
  label,
  value,
  critical = false,
  emphasis = false,
}: {
  label: string;
  value: string;
  critical?: boolean;
  emphasis?: boolean;
}) {
  const tone = critical ? "text-critical-text" : emphasis ? "text-ink" : "text-ink-dim";

  return (
    <div className="bg-page px-4 py-3">
      <dt className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{label}</dt>
      <dd className={`mt-1 font-mono text-md ${tone}`}>{value}</dd>
    </div>
  );
}

/** A heading that names a section and says what is in it, in one sentence. */
const SCOPED_LINK_CLASS = "text-brand-text underline-offset-2 hover:underline";

function SectionHeading({ title, caption }: { title: string; caption: string }) {
  return (
    <div className="px-4 pt-6 pb-2">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">{title}</h2>
      <p className="mt-0.5 text-ink-dim text-sm">{caption}</p>
    </div>
  );
}

function TrendSection({
  plots,
  serviceName,
  bucketCount,
  markers,
}: {
  plots: readonly TrendPlot[];
  serviceName: string;
  bucketCount: number;
  markers: readonly DeployMarker[];
}) {
  return (
    <>
      <SectionHeading
        title="Over the window"
        caption={
          bucketCount === 0
            ? "No buckets in this window — the rollup has nothing for this service yet."
            : `Request rate, failure share and duration across ${bucketCount} ${bucketCount === 1 ? "bucket" : "buckets"}. Three charts, because three units — and three lines on the last, because the shape of a distribution is p50, p95 and p99 together.${markers.length > 0 ? ` Dashed rules are deploys of ${serviceName}.` : ""}`
        }
      />
      {bucketCount > 0 ? (
        <div className="grid gap-4 px-4 lg:grid-cols-3">
          {plots.map((plot) => (
            <TrendPanel
              key={plot.metric}
              plot={plot}
              markers={markers}
              description={describePlot(plot, serviceName, bucketCount)}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}

function TrendPanel({
  plot,
  markers,
  description,
}: {
  plot: TrendPlot;
  markers: readonly DeployMarker[];
  description: string;
}) {
  return (
    <section className="rounded-sm border border-line p-3">
      <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{plot.label}</h3>
      {plot.flatAtZero ? (
        // Held at the chart's own height so a row of three panels stays level.
        <p className="flex items-center text-ink-dim text-sm" style={{ height: CHART_HEIGHT }}>
          {plot.zeroText}
        </p>
      ) : (
        <TrendChart
          label={plot.lineNames}
          seriesToken={plot.seriesToken}
          data={plot.data}
          markers={markers}
          formatValue={AXIS_FORMAT[plot.metric]}
          description={description}
        />
      )}
    </section>
  );
}

/**
 * The duration heatmap, present only where the customer sends a histogram.
 *
 * **Absent rather than empty** (OD7). Drawing an empty grid for the majority who send no
 * OTLP histogram would say this service had no traffic, which is a different and false
 * claim — the three percentile lines above already carry the common case.
 */
function HeatmapSection({
  serviceName,
  heatmap,
}: {
  serviceName: string;
  heatmap?: DurationHeatmapResponse;
}) {
  if (!heatmap?.available || heatmap.columns.length === 0) return null;

  return (
    <>
      <SectionHeading
        title="Where the time went"
        caption={`The shape of ${serviceName}'s latency in each bucket, from the ${heatmap.metric_name} histogram it reports. Each column is scaled to its own traffic, so a tail is visible in a quiet hour as well as a busy one.`}
      />
      <div className="px-4">
        <DurationHeatmap
          bounds={heatmap.bounds}
          columns={heatmap.columns}
          unit={heatmap.unit}
          label={`${serviceName}'s latency distribution across ${heatmap.columns.length} buckets, fastest at the bottom.`}
        />
      </div>
    </>
  );
}

function DependencySection({
  serviceName,
  source,
  callers,
  callees,
  hostGroups,
}: {
  serviceName: string;
  source: PanelSource<ServiceEdge>;
  callers: readonly Dependency[];
  callees: readonly Dependency[];
  hostGroups: ReadonlyMap<string, HostGroup>;
}) {
  return (
    <>
      <SectionHeading
        title="Dependencies"
        caption={describeDependencies(source, callers.length, callees.length)}
      />

      {source.status === "error" ? (
        <div className="px-4">
          <InlineFailure
            title="Dependencies could not be loaded"
            error={source.error}
            onRetry={source.onRetry}
          />
        </div>
      ) : source.status === "pending" ? (
        <div className="px-4">
          <SkeletonRows rows={3} label={`Loading dependencies of ${serviceName}`} />
        </div>
      ) : (
        <div className="grid gap-4 px-4 md:grid-cols-2">
          <DependencyList
            title="Called by"
            direction="in"
            dependencies={callers}
            hostGroups={hostGroups}
            empty="Nothing called this service in the window."
          />
          <DependencyList
            title="Calls"
            direction="out"
            dependencies={callees}
            hostGroups={hostGroups}
            empty="This service called nothing else in the window."
          />
        </div>
      )}
    </>
  );
}

function describeDependencies(
  source: PanelSource<ServiceEdge>,
  callerCount: number,
  calleeCount: number,
): string {
  if (source.status === "pending") return "Reading the window's dependency graph.";
  if (source.status === "error") return "The dependency graph could not be read.";
  if (callerCount === 0 && calleeCount === 0) {
    return "No call was seen from or to this service in the window. A service that talks to nobody legitimately shows nothing here.";
  }
  return `${callerCount} calling in, ${calleeCount} called out. Every arrow was derived from spans you already send.`;
}

function DependencyList({
  title,
  direction,
  dependencies,
  hostGroups,
  empty,
}: {
  title: string;
  direction: "in" | "out";
  dependencies: readonly Dependency[];
  hostGroups: ReadonlyMap<string, HostGroup>;
  empty: string;
}) {
  const Arrow = direction === "in" ? ArrowRight : ArrowLeft;

  return (
    <section>
      <h3 className="flex items-center gap-1.5 border-line border-b pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <Arrow aria-hidden size={11} className="shrink-0 text-solid" />
        {title}
      </h3>
      {dependencies.length === 0 ? (
        <p className="py-2 text-ink-dim text-sm">{empty}</p>
      ) : (
        <ul>
          {dependencies.map((dependency) => (
            <li key={dependencyKey(direction, dependency)}>
              <DependencyLine dependency={dependency} hosts={hostGroups.get(dependency.peer)} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * What a collapsed row stands for, on hover. The row aggregates and the title gives
 * back the detail storage kept — the addresses themselves, which is the only place a
 * reader can still see them.
 */
function describeHosts(hosts: HostGroup): string {
  const count = hosts.addresses.length;
  return `${count} ${count === 1 ? "host" : "hosts"} reached by address, with no telemetry of their own — ${hosts.addresses.join(", ")}. Addresses change on restart, so they are listed as one dependency rather than one row each.`;
}

function DependencyLine({ dependency, hosts }: { dependency: Dependency; hosts?: HostGroup }) {
  const { peer, edge } = dependency;
  const failing = edge.error_count > 0;

  const name = isPlaceholderService(peer) ? (
    // Something we only see from the outside: it has no screen to open.
    <span className="truncate text-ink-dim italic" title={hosts && describeHosts(hosts)}>
      {peer}
    </span>
  ) : (
    <Link
      to="/services/$name"
      params={{ name: peer }}
      className="truncate text-ink underline-offset-2 hover:underline"
    >
      {peer}
    </Link>
  );

  return (
    <div className="flex items-center gap-2 border-line-subtle border-b py-1.5 text-sm">
      <span className="flex min-w-0 flex-1 items-baseline gap-2">
        {name}
        {hosts ? (
          // How many there are is the fact a collapsed row would otherwise have hidden.
          <span className="shrink-0 font-mono text-2xs text-ink-dim">
            {hosts.addresses.length} {hosts.addresses.length === 1 ? "instance" : "instances"}
          </span>
        ) : null}
        {edge.edge_kind === EDGE_QUEUE ? (
          <span className="shrink-0 font-mono text-2xs text-ink-dim uppercase tracking-caps">
            queue
          </span>
        ) : null}
      </span>
      <span className={`${RATE_COLUMN} font-mono text-2xs text-ink-dim`}>
        {formatRatePerMinute(edge.calls_per_minute)}
      </span>
      <span
        className={`${ERRORS_COLUMN} font-mono text-2xs ${
          failing ? "text-critical-text" : "text-ink-dim"
        }`}
      >
        {formatErrors(edge.error_count, edge.error_rate)}
      </span>
      <span className={`${LATENCY_COLUMN} font-mono text-2xs text-ink`}>
        {formatDuration(edge.p95_ms)}
      </span>
    </div>
  );
}

/**
 * How many error groups the panel draws. The caption carries the true totals, so the
 * cap costs no information — it stops one noisy service pushing the endpoint table,
 * which is the other half of this screen, off the bottom of the page.
 */
const ERRORS_SHOWN = 5;

const OCCURRENCES_COLUMN = "w-20 shrink-0 text-right";
const LAST_SEEN_COLUMN = "w-24 shrink-0 text-right";

/**
 * The exceptions thrown in this service, most frequent first.
 *
 * Server-narrowed by name. Filtering the project-wide list here would under-report,
 * because that list is capped and a quiet service's groups fall off the end of it —
 * which is worse than showing nothing, because nothing looks like nothing happened.
 */
function ErrorSection({
  serviceName,
  source,
}: {
  serviceName: string;
  source: PanelSource<ErrorGroupSummary>;
}) {
  const shown = source.data.slice(0, ERRORS_SHOWN);
  const hidden = source.data.length - shown.length;

  return (
    <>
      <SectionHeading title="Errors" caption={describeErrorPanel(source)} />

      {source.status === "error" ? (
        <div className="px-4">
          <InlineFailure
            title="Errors could not be loaded"
            error={source.error}
            onRetry={source.onRetry}
          />
        </div>
      ) : source.status === "pending" ? (
        <div className="px-4">
          <SkeletonRows rows={3} label={`Loading errors in ${serviceName}`} />
        </div>
      ) : shown.length === 0 ? null : (
        <div className="px-4">
          <ul>
            {shown.map((group) => (
              <li key={group.group_id}>
                <ErrorLine group={group} />
              </li>
            ))}
          </ul>
          {hidden > 0 ? (
            <p className="py-2 text-ink-dim text-sm">
              {hidden} less frequent {hidden === 1 ? "error is" : "errors are"} not shown. The
              Errors screen has every one.
            </p>
          ) : null}
        </div>
      )}
    </>
  );
}

function describeErrorPanel(source: PanelSource<ErrorGroupSummary>): string {
  if (source.status === "pending") return "Reading this service's exceptions.";
  if (source.status === "error") return "This service's exceptions could not be read.";
  return describeServiceErrors(source.data);
}

function ErrorLine({ group }: { group: ErrorGroupSummary }) {
  return (
    <div className="flex items-center gap-3 border-line-subtle border-b py-1.5 text-sm">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate font-medium text-ink">{group.exception_type}</span>
        {/* The template, not the latest message: the template is what the group *is*. */}
        <span className="truncate text-ink-dim text-xs" title={group.message_template}>
          {group.message_template}
        </span>
      </span>
      <span className={`${OCCURRENCES_COLUMN} font-mono text-2xs text-ink`}>
        {formatCount(group.occurrences)}
      </span>
      <span className={`${LAST_SEEN_COLUMN} font-mono text-2xs text-ink-dim`}>
        {formatRelativeTime(group.last_seen)}
      </span>
    </div>
  );
}

function EndpointColumnHeader() {
  return (
    <div className="flex items-center border-line border-y bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
      <span className="min-w-0 flex-1">Endpoint</span>
      <span className={RATE_COLUMN}>Req/min</span>
      <span className={ERRORS_COLUMN}>Errors</span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY}`}>p50</span>
      <span className={LATENCY_COLUMN}>p95</span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY}`}>p99</span>
    </div>
  );
}

function EndpointLine({ endpoint }: { endpoint: EndpointHealth }) {
  return (
    <div className="flex h-full items-center border-line-subtle border-b px-4 text-sm">
      <span className="flex min-w-0 flex-1 items-baseline gap-2">
        {/* The kind is here because SERVER and CONSUMER can share a name and are not
            the same endpoint; the API keys them separately for that reason. */}
        <span className="shrink-0 font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {endpoint.span_kind}
        </span>
        <span className="truncate text-ink">{endpoint.endpoint}</span>
      </span>
      <MetricCells metrics={endpoint} />
    </div>
  );
}

/** The numbers, in the same columns the overview puts them in. */
function MetricCells({ metrics }: { metrics: HealthMetrics }) {
  const failing = metrics.error_count > 0;

  return (
    <>
      <span className={`${RATE_COLUMN} font-mono text-2xs text-ink-dim`}>
        {formatRatePerMinute(metrics.requests_per_minute)}
      </span>
      <span
        className={`${ERRORS_COLUMN} font-mono text-2xs ${
          failing ? "text-critical-text" : "text-ink-dim"
        }`}
      >
        {formatErrors(metrics.error_count, metrics.error_rate)}
      </span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY} font-mono text-2xs text-ink-dim`}>
        {formatDuration(metrics.p50_ms)}
      </span>
      {/* p95 is the number the ranking is about, so it reads as text rather than as
          metadata. */}
      <span className={`${LATENCY_COLUMN} font-mono text-2xs text-ink`}>
        {formatDuration(metrics.p95_ms)}
      </span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY} font-mono text-2xs text-ink-dim`}>
        {formatDuration(metrics.p99_ms)}
      </span>
    </>
  );
}

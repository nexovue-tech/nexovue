import { Link } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatDuration } from "@/shared/format/duration";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type { EndpointHealth, HealthMetrics, ServiceEdge, ServiceHealth, TrendPoint } from "./api";
import {
  EDGE_QUEUE,
  isPlaceholderService,
  RANKED_PAGE_LIMIT,
  useRankedServices,
  useServiceDependencies,
  useServiceEndpoints,
  useServiceTrend,
} from "./api";
import { formatCount, formatErrorRate, formatErrors, formatRatePerMinute } from "./health-format";
import type { Dependency, DetailState } from "./service-detail";
import {
  dependencyKey,
  describeEndpoints,
  describeService,
  describeSilence,
  detailState,
  findRankedService,
  neighboursOf,
} from "./service-detail";
import { CHART_HEIGHT, TrendChart } from "./TrendChart";
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
  errorRate: (percent) => formatErrorRate(percent / PERCENT),
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
  const timeWindow = useLiveWindow(rangeId);

  const ranked = useRankedServices(projectId, timeWindow);
  const trend = useServiceTrend(projectId, serviceName, timeWindow);
  const endpoints = useServiceEndpoints(projectId, serviceName, timeWindow);
  const dependencies = useServiceDependencies(projectId, timeWindow);

  const points = trend.data?.points ?? [];
  const endpointRows = endpoints.data?.endpoints ?? [];
  const row = findRankedService(ranked.data?.services ?? [], serviceName);

  const state = detailState({
    isPending: ranked.isPending || trend.isPending || endpoints.isPending,
    isError: ranked.isError || trend.isError || endpoints.isError,
    hasTrend: points.length > 0,
    hasEndpoints: endpointRows.length > 0,
    hasRankedRow: row !== undefined,
  });

  return (
    <ServiceDetailView
      serviceName={serviceName}
      windowLabel={timeRangeById(rangeId).label}
      state={state}
      error={ranked.error ?? trend.error ?? endpoints.error}
      onRetry={() => {
        void ranked.refetch();
        void trend.refetch();
        void endpoints.refetch();
      }}
      row={row}
      points={points}
      endpoints={endpointRows}
      dependencies={{
        status: dependencies.isPending ? "pending" : dependencies.isError ? "error" : "success",
        edges: dependencies.data?.edges ?? [],
        error: dependencies.error,
        onRetry: () => void dependencies.refetch(),
      }}
    />
  );
}

/** The dependency panel loads on its own clock; its failure is not the screen's. */
export interface DependencySource {
  status: "pending" | "error" | "success";
  edges: readonly ServiceEdge[];
  error?: unknown;
  onRetry: () => void;
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
  endpoints: readonly EndpointHealth[];
  dependencies: DependencySource;
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
  endpoints,
  dependencies,
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
  const neighbours = useMemo(
    () => neighboursOf(dependencies.edges, serviceName),
    [dependencies.edges, serviceName],
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

            <TrendSection plots={plots} serviceName={serviceName} bucketCount={points.length} />

            <DependencySection
              serviceName={serviceName}
              source={dependencies}
              callers={neighbours.callers}
              callees={neighbours.callees}
            />

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
        <nav className="flex shrink-0 items-baseline gap-3 text-sm">
          <Link
            to="/traces"
            search={{ service: serviceName }}
            className="text-brand-text underline-offset-2 hover:underline"
          >
            Traces
          </Link>
          <Link
            to="/traces"
            search={{ service: serviceName, status: "ERROR" }}
            className="text-brand-text underline-offset-2 hover:underline"
          >
            Failed traces
          </Link>
        </nav>
      </div>

      <p className="mt-1 text-ink-dim text-sm">
        {state === "ready"
          ? describeService(row, windowLabel, RANKED_PAGE_LIMIT)
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
}: {
  plots: readonly TrendPlot[];
  serviceName: string;
  bucketCount: number;
}) {
  return (
    <>
      <SectionHeading
        title="Over the window"
        caption={
          bucketCount === 0
            ? "No buckets in this window — the rollup has nothing for this service yet."
            : `Request rate, failure share and p95 across ${bucketCount} ${bucketCount === 1 ? "bucket" : "buckets"}. Three charts, because three units.`
        }
      />
      {bucketCount > 0 ? (
        <div className="grid gap-4 px-4 lg:grid-cols-3">
          {plots.map((plot) => (
            <TrendPanel
              key={plot.metric}
              plot={plot}
              description={describePlot(plot, serviceName, bucketCount)}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}

function TrendPanel({ plot, description }: { plot: TrendPlot; description: string }) {
  return (
    <section className="rounded-sm border border-line p-3">
      <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{plot.label}</h3>
      {plot.flatAtZero ? (
        // Held at the chart's own height so a row of three panels stays level.
        <p className="flex items-center text-ink-dim text-sm" style={{ height: CHART_HEIGHT }}>
          {plot.zeroText}
        </p>
      ) : (
        <TrendChart plot={plot} formatValue={AXIS_FORMAT[plot.metric]} description={description} />
      )}
    </section>
  );
}

function DependencySection({
  serviceName,
  source,
  callers,
  callees,
}: {
  serviceName: string;
  source: DependencySource;
  callers: readonly Dependency[];
  callees: readonly Dependency[];
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
            empty="Nothing called this service in the window."
          />
          <DependencyList
            title="Calls"
            direction="out"
            dependencies={callees}
            empty="This service called nothing else in the window."
          />
        </div>
      )}
    </>
  );
}

function describeDependencies(
  source: DependencySource,
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
  empty,
}: {
  title: string;
  direction: "in" | "out";
  dependencies: readonly Dependency[];
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
              <DependencyLine dependency={dependency} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DependencyLine({ dependency }: { dependency: Dependency }) {
  const { peer, edge } = dependency;
  const failing = edge.error_count > 0;

  const name = isPlaceholderService(peer) ? (
    // Something we only see from the outside: it has no screen to open.
    <span className="truncate text-ink-dim italic">{peer}</span>
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

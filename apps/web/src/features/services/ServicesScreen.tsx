import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronRight } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows, SkeletonText } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { formatDuration } from "@/shared/format/duration";
import { formatErrors } from "@/shared/format/health";
import { formatRatePerMinute } from "@/shared/format/rate";
import { TIME_RANGES, timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type {
  EndpointGroup,
  EndpointHealth,
  HealthMetrics,
  ServiceHealth,
  TrendPoint,
} from "./api";
import { useEndpointsByService, useServiceList, useServiceTrend } from "./api";
import type { ServiceOrder, ServiceRow } from "./service-rows";
import {
  buildServiceRows,
  describeServices,
  describeVolume,
  ORDER_OPTIONS,
  rowHeight,
} from "./service-rows";

const OVERSCAN_ROWS = 10;

/** Two placeholder endpoint lines. Described, not looped: they differ in width. */
const PENDING_ENDPOINT_ROWS = [
  { id: "endpoint-placeholder-1", width: "wide" },
  { id: "endpoint-placeholder-2", width: "half" },
] as const;

/* Column widths, declared once so the header and every row cannot drift apart. */
const RATE_COLUMN = "w-20 shrink-0 text-right";
const ERRORS_COLUMN = "w-28 shrink-0 text-right";
const LATENCY_COLUMN = "w-16 shrink-0 text-right";
/* The sparkline is 104px and the gutter before it 12px, so the cell is 116px — `w-26` held
   only the drawing and pushed it 12px into the row's own right padding, which put the
   column 4px from the edge while every other column sat 16px in. Reserved on every row so
   the columns to its left do not shift when one service has no trend to draw. */
const TREND_COLUMN = "w-29 shrink-0 pl-3";
/* Reserved on every row, marked or not, so the names below each other line up. */
const MARK_COLUMN = "flex w-2 shrink-0 justify-center";
const WIDE_ONLY = "hidden lg:block";

/**
 * The front door: every service sending traces, worst first, with its endpoints
 * underneath.
 *
 * The route component owns the two selections and the queries; everything visible is in
 * `ServicesView`, which is a function of its props and nothing else.
 */
export function ServicesScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow(rangeId);

  const [order, setOrder] = useState<ServiceOrder>("worst");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());

  const services = useServiceList(projectId, timeWindow);
  const trendByService = useServiceTrend(projectId, timeWindow);

  // Sorted so the request set is a function of *which* services are open, not the
  // order they were opened in — otherwise every toggle rebuilds every query.
  const expandedNames = useMemo(() => [...expanded].sort(), [expanded]);
  const endpointsByService = useEndpointsByService(projectId, expandedNames, timeWindow);

  function toggleService(serviceName: string) {
    setExpanded((open) => {
      const next = new Set(open);
      if (!next.delete(serviceName)) next.add(serviceName);
      return next;
    });
  }

  return (
    <ServicesView
      order={order}
      onOrderChange={setOrder}
      windowLabel={timeRangeById(rangeId).label}
      widestWindow={rangeId === TIME_RANGES[TIME_RANGES.length - 1].id}
      services={services.data?.services ?? []}
      isPending={services.isPending}
      isError={services.isError}
      error={services.error}
      onRetry={() => void services.refetch()}
      expanded={expanded}
      onToggleService={toggleService}
      endpointsByService={endpointsByService}
      trendByService={trendByService}
    />
  );
}

interface ServicesViewProps {
  order: ServiceOrder;
  onOrderChange: (order: ServiceOrder) => void;
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  /**
   * Whether the widest range is already selected, which is what separates the two empty
   * screens. See `EmptyServices`.
   */
  widestWindow?: boolean;
  services: readonly ServiceHealth[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  expanded: ReadonlySet<string>;
  onToggleService: (serviceName: string) => void;
  endpointsByService: ReadonlyMap<string, EndpointGroup>;
  /** Keyed by service name. A service missing from it has no trend and renders the rule. */
  trendByService?: ReadonlyMap<string, readonly TrendPoint[]>;
}

/**
 * A failing row plots what it is failing at; a healthy one plots what it is doing.
 *
 * The first version drew request rate on every row and stroked it with the row's
 * severity, so a service with errors showed a *red* line that was its traffic. A red
 * line reads as errors, and that one said something the data did not. Colour and data
 * now agree, because the colour picks the series rather than decorating it.
 *
 * **Two ceilings, not one.** An error rate is a fraction and a request rate is a count
 * per minute, so no single number can scale both. Each kind therefore shares a ceiling
 * with its own kind, which is the only comparability that ever meant anything —
 * `checkout`'s request rate was never comparable to `flagd`'s error rate.
 */
export interface TrendCeilings {
  rate: number;
  errorRate: number;
}

/**
 * Computed from the services actually loaded. A page cap of 100 means the scale is right
 * for what is shown; a taller service arriving on a later page would rescale its column,
 * which is worth remembering when filters start changing what is loaded.
 */
export function trendCeilings(
  services: readonly ServiceHealth[],
  trendByService: ReadonlyMap<string, readonly TrendPoint[]> | undefined,
): TrendCeilings {
  const ceilings: TrendCeilings = { rate: 0, errorRate: 0 };
  for (const service of services) {
    for (const point of trendByService?.get(service.service_name) ?? []) {
      if (point.requests_per_minute > ceilings.rate) ceilings.rate = point.requests_per_minute;
      if (point.error_rate > ceilings.errorRate) ceilings.errorRate = point.error_rate;
    }
  }
  return ceilings;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function ServicesView({
  order,
  onOrderChange,
  windowLabel,
  widestWindow = false,
  services,
  isPending,
  isError,
  error,
  onRetry,
  expanded,
  onToggleService,
  endpointsByService,
  trendByService,
}: ServicesViewProps) {
  const rows = buildServiceRows(services, order, expanded, endpointsByService);
  const ceilings = trendCeilings(services, trendByService);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => rowHeight(rows[index]),
    overscan: OVERSCAN_ROWS,
    getItemKey: (index) => rows[index].key,
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <div className="flex items-baseline justify-between gap-6">
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Services</h1>
          <OrderToggle order={order} onOrderChange={onOrderChange} />
        </div>
        <p className="mt-1 text-ink-dim text-sm">
          {isPending || isError
            ? `Every service you send traces from, over the last ${windowLabel}.`
            : describeServices(services.length, windowLabel, order)}
        </p>
      </header>

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={10} label="Loading services" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure title="Services could not be loaded" error={error} onRetry={onRetry} />
        </div>
      ) : services.length === 0 ? (
        <EmptyServices widestWindow={widestWindow} windowLabel={windowLabel} />
      ) : (
        <>
          <ColumnHeader />

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
            <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((item) => (
                <VirtualRow
                  key={item.key}
                  row={rows[item.index]}
                  offsetTop={item.start}
                  height={item.size}
                  onToggleService={onToggleService}
                  endpointsByService={endpointsByService}
                  trendByService={trendByService}
                  ceilings={ceilings}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Two empty screens, not one.
 *
 * "Nothing has ever arrived" and "nothing arrived in these five minutes" are different
 * facts and want different actions: the first is an onboarding moment and should offer
 * setup; the second is a window too narrow and should offer to widen it. A single screen
 * covering both tells a customer with eleven services that they have none.
 *
 * **The signal is a heuristic, and deliberately a conservative one.** Nothing in the API
 * says whether a project has ever received telemetry, so the widest range coming back
 * empty is the closest available proxy. It is wrong in one direction only — a project
 * silent for more than seven days reads as never having reported — and that error costs
 * a reader a sentence, where the opposite error would send a customer with working
 * instrumentation back to the setup page.
 */
function EmptyServices({
  widestWindow,
  windowLabel,
}: {
  widestWindow: boolean;
  windowLabel: string;
}) {
  if (widestWindow) {
    return (
      <EmptyState
        title="No services yet"
        detail="Nothing has sent a trace to this project. Point an OpenTelemetry SDK at your endpoint and the first service appears here within about a minute."
      />
    );
  }

  return (
    <EmptyState
      title={`No services in the last ${windowLabel}`}
      detail="Nothing reported in the selected period. Widen the time range, or check that your services are still exporting — a service that stops reporting disappears from this list rather than turning red."
    />
  );
}

/**
 * Which ranking is in force, written as a sentence rather than dressed as a form —
 * the same idiom as the top bar's `PROJECT production ⌄`.
 */
function OrderToggle({
  order,
  onOrderChange,
}: {
  order: ServiceOrder;
  onOrderChange: (order: ServiceOrder) => void;
}) {
  return (
    <div className="flex shrink-0 items-baseline gap-1.5 font-mono text-2xs uppercase tracking-caps">
      <span className="text-ink-dim">Rank</span>
      {ORDER_OPTIONS.map((option) => (
        <button
          key={option.id}
          type="button"
          aria-pressed={order === option.id}
          onClick={() => onOrderChange(option.id)}
          className={`rounded-sm border px-1.5 py-0.5 ${
            order === option.id
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

function ColumnHeader() {
  return (
    <div className="flex shrink-0 items-center border-line border-b bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
      <span className="min-w-0 flex-1">Service</span>
      <span className={RATE_COLUMN}>Req/min</span>
      <span className={ERRORS_COLUMN}>Errors</span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY}`}>p50</span>
      <span className={LATENCY_COLUMN}>p95</span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY}`}>p99</span>
      <span className={TREND_COLUMN}>Trend</span>
    </div>
  );
}

function VirtualRow({
  row,
  offsetTop,
  height,
  onToggleService,
  endpointsByService,
  trendByService,
  ceilings,
}: {
  row: ServiceRow;
  offsetTop: number;
  height: number;
  onToggleService: (serviceName: string) => void;
  endpointsByService: ReadonlyMap<string, EndpointGroup>;
  trendByService?: ReadonlyMap<string, readonly TrendPoint[]>;
  ceilings: TrendCeilings;
}) {
  return (
    <div
      className="absolute inset-x-0 top-0"
      style={{ height, transform: `translateY(${offsetTop}px)` }}
    >
      {row.kind === "service" ? (
        <ServiceLine
          service={row.service}
          expanded={row.expanded}
          onToggle={() => onToggleService(row.service.service_name)}
          trend={trendByService?.get(row.service.service_name)}
          ceilings={ceilings}
        />
      ) : row.kind === "endpoint" ? (
        <EndpointLine endpoint={row.endpoint} />
      ) : (
        <EndpointNote
          note={row.note}
          onRetry={() => endpointsByService.get(row.serviceName)?.refetch()}
        />
      )}
    </div>
  );
}

function ServiceLine({
  service,
  expanded,
  onToggle,
  trend,
  ceilings,
}: {
  service: ServiceHealth;
  expanded: boolean;
  onToggle: () => void;
  trend?: readonly TrendPoint[];
  ceilings: TrendCeilings;
}) {
  const failing = service.error_count > 0;

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className="flex h-full w-full items-center border-line-subtle border-b px-4 text-left text-sm hover:bg-hover"
    >
      <span className="flex min-w-0 flex-1 items-center gap-1.5">
        <ChevronRight
          aria-hidden
          size={12}
          className={`shrink-0 text-solid transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        <SeverityMark failing={service.error_count > 0} />
        <span className="truncate text-ink">{service.service_name}</span>
      </span>
      <MetricCells metrics={service} />
      <span className={TREND_COLUMN}>
        <Sparkline
          points={(trend ?? []).map((point) =>
            failing ? point.error_rate : point.requests_per_minute,
          )}
          max={failing ? ceilings.errorRate : ceilings.rate}
          tone={failing ? "critical" : "series"}
          label={`${service.service_name} ${failing ? "error rate" : "request rate"} over the window`}
        />
      </span>
    </button>
  );
}

/**
 * Health is the absence of a mark.
 *
 * A healthy row carries nothing: colour means something needs you, and a column of marks
 * on every row is a tool shouting that nothing is happening. The space is still reserved,
 * so the names line up whether a row is marked or not.
 *
 * **One tier, not two.** `design-system.md` distinguishes critical from warning by shape
 * as well as hue, and nothing in this product defines what a *warning* service is — no
 * error-rate threshold exists anywhere in the code, and inventing one here would be
 * deciding product behavior in a component. The mark is therefore binary, exactly as the
 * errors cell beside it already is, and the shape distinction waits for a threshold
 * somebody has actually decided.
 */
function SeverityMark({ failing }: { failing: boolean }) {
  return (
    <span className={MARK_COLUMN} aria-hidden>
      {failing ? (
        <span data-severity="critical" className="size-1.75 rounded-full bg-critical-solid" />
      ) : null}
    </span>
  );
}

function EndpointLine({ endpoint }: { endpoint: EndpointHealth }) {
  return (
    <div className="flex h-full items-center border-line-subtle border-b bg-surface px-4 text-sm">
      <span className="flex min-w-0 flex-1 items-baseline gap-2 pl-4.5">
        {/* The kind is here because SERVER and CONSUMER can share a name and are not
            the same endpoint; the API keys them separately for that reason. */}
        <span className="shrink-0 font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {endpoint.span_kind}
        </span>
        <span className="truncate text-ink-dim">{endpoint.endpoint}</span>
      </span>
      <MetricCells metrics={endpoint} />
      {/* No per-endpoint series exists; the column is reserved so the numbers stay aligned
          with the service row above. */}
      <span className={TREND_COLUMN} aria-hidden />
    </div>
  );
}

/** The numbers, in the same columns whether the row is a service or an endpoint. */
function MetricCells({ metrics }: { metrics: HealthMetrics }) {
  const failing = metrics.error_count > 0;

  return (
    <>
      <span
        className={`${RATE_COLUMN} font-mono text-2xs text-ink-dim`}
        title={describeVolume(metrics.request_count)}
      >
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
      {/* p95 is the one the ranking and the toggle are about, so it is the one that
          reads as text rather than as metadata. */}
      <span className={`${LATENCY_COLUMN} font-mono text-2xs text-ink`}>
        {formatDuration(metrics.p95_ms)}
      </span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY} font-mono text-2xs text-ink-dim`}>
        {formatDuration(metrics.p99_ms)}
      </span>
    </>
  );
}

/**
 * A silent service and one whose endpoints failed to load are different facts, and a
 * screen that shows the same blank space for both has lost the one that matters.
 */
const NOTE_TEXT: Record<"empty" | "failed", string> = {
  empty: "No endpoints reported in this window.",
  failed: "Endpoints could not be loaded.",
};

function EndpointNote({
  note,
  onRetry,
}: {
  note: "pending" | "empty" | "failed";
  onRetry: () => void;
}) {
  if (note === "pending") {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-label="Loading endpoints"
        className="h-full bg-surface"
      >
        {PENDING_ENDPOINT_ROWS.map((placeholder) => (
          <div
            key={placeholder.id}
            className="flex h-7 items-center gap-2 border-line-subtle border-b px-4"
          >
            <span className="flex-1 pl-4.5">
              <SkeletonText width={placeholder.width} />
            </span>
            <span className="w-20 shrink-0">
              <SkeletonText width="full" />
            </span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex h-full items-center gap-3 border-line-subtle border-b bg-surface px-4 text-sm">
      <span className={`pl-4.5 ${note === "failed" ? "text-critical-text" : "text-ink-dim"}`}>
        {NOTE_TEXT[note]}
      </span>
      {note === "failed" ? (
        <button
          type="button"
          onClick={onRetry}
          className="text-brand-text underline-offset-2 hover:underline"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

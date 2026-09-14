import { useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronRight } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { Changed } from "@/shared/components/Changed";
import { Delta } from "@/shared/components/Delta";
import { EmptyState } from "@/shared/components/EmptyState";
import type { FilterValues } from "@/shared/components/FilterBar";
import { appliedFilters, FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { ReorderNotice } from "@/shared/components/ReorderNotice";
import { SkeletonRows, SkeletonText } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { deltaOf } from "@/shared/delta";
import { filterParams, parseFilterValues, withFilter } from "@/shared/filters";
import { formatDuration } from "@/shared/format/duration";
import { formatErrors } from "@/shared/format/health";
import { formatRatePerMinute } from "@/shared/format/rate";
import { useSettledOrder } from "@/shared/live-updates";
import { useEverReported } from "@/shared/projects";
import { previousWindow, useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";

import type {
  EndpointGroup,
  EndpointHealth,
  HealthMetrics,
  ServiceHealth,
  TrendPoint,
} from "./api";
import { useEndpointsByService, useServiceList, useServiceTrend } from "./api";
import type { ServiceOrder } from "./filters";
import { ORDER_OPTIONS, parseServiceOrder, SERVICE_FILTERS } from "./filters";
import type { ServiceRow } from "./service-rows";
import { buildServiceRows, describeServices, describeVolume, rowHeight } from "./service-rows";
import type { ServiceSeverity } from "./severity";
import { SEVERITY_LEGEND, serviceSeverity } from "./severity";

const OVERSCAN_ROWS = 10;

/** Two placeholder endpoint lines. Described, not looped: they differ in width. */
const PENDING_ENDPOINT_ROWS = [
  { id: "endpoint-placeholder-1", width: "wide" },
  { id: "endpoint-placeholder-2", width: "half" },
] as const;

/* Column widths, declared once so the header and every row cannot drift apart. */
/* Three of these can carry a delta beside the reading, so they are sized for the
   widest one — "+100%" — rather than for the number alone. Reserved on every row,
   marked or not, so a row that changed does not shift the columns of the rows
   around it. p50 and p99 carry none and keep their original width. */
const RATE_COLUMN = "w-32 shrink-0 text-right";
const ERRORS_COLUMN = "w-40 shrink-0 text-right";
const LATENCY_COLUMN = "w-16 shrink-0 text-right";
const P95_COLUMN = "w-28 shrink-0 text-right";
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
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();
  const navigate = useNavigate();

  // The route's own id, not its path: `/services` renders inside the pathless shell.
  const search = useSearch({ from: "/shell/services" });
  const filters = useMemo(() => parseFilterValues(SERVICE_FILTERS, search), [search]);
  const order = parseServiceOrder(search.order);

  // `replace`, not push: the back button belongs to the screen a person came
  // from, not to every control they tried on the way.
  const show = (next: FilterValues, nextOrder: ServiceOrder = order) =>
    void navigate({
      to: "/services",
      search: () => ({
        ...filterParams(next),
        ...(nextOrder === "worst" ? {} : { order: nextOrder }),
      }),
      replace: true,
    });

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());

  // The ordering travels with the filters: the server cuts the list, so both
  // have to reach it before the cut.
  const narrowing = useMemo(
    () => ({ ...filterParams(filters), ...(order === "worst" ? {} : { order }) }),
    [filters, order],
  );

  const services = useServiceList(projectId, timeWindow, narrowing);
  const trendByService = useServiceTrend(projectId, timeWindow);

  // A fact, not a guess about the selected range. See `EmptyServices`.
  const everReported = useEverReported(projectId);

  // The same read over the hour before this one. A second request rather than a
  // wider one: comparing two windows means asking the rollup for two windows,
  // and the ranking of the earlier one is irrelevant — only its numbers are.
  const earlier = useServiceList(projectId, previousWindow(timeWindow), narrowing);
  const previousByService = useMemo(
    () => new Map((earlier.data?.services ?? []).map((row) => [row.service_name, row])),
    [earlier.data],
  );

  // Sorted so the request set is a function of *which* services are open, not the
  // order they were opened in — otherwise every toggle rebuilds every query.
  const expandedNames = useMemo(() => [...expanded].sort(), [expanded]);
  const endpointsByService = useEndpointsByService(projectId, expandedNames, timeWindow, narrowing);

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
      onOrderChange={(next) => show(filters, next)}
      filters={filters}
      onFilterChange={(key, value) => show(withFilter(filters, key, value))}
      onClearFilters={() => show({})}
      windowLabel={windowPhrase}
      everReported={everReported}
      services={services.data?.services ?? []}
      isPending={services.isPending}
      isError={services.isError}
      error={services.error}
      onRetry={() => void services.refetch()}
      expanded={expanded}
      onToggleService={toggleService}
      endpointsByService={endpointsByService}
      trendByService={trendByService}
      previousByService={previousByService}
    />
  );
}

interface ServicesViewProps {
  order: ServiceOrder;
  onOrderChange: (order: ServiceOrder) => void;
  filters: FilterValues;
  onFilterChange: (key: string, value: string | undefined) => void;
  onClearFilters: () => void;
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  /**
   * Whether anything has ever arrived for this project, which is what separates the two
   * empty screens. `undefined` while unknown. See `EmptyServices`.
   */
  everReported?: boolean;
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
  /**
   * The same services over the window before this one, keyed by name.
   *
   * A service missing from it is one that did not report then, which is not the
   * same as one that reported zero — so its row carries no delta at all.
   */
  previousByService?: ReadonlyMap<string, ServiceHealth>;
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
/** What a held row is identified by — the name is what a reader aims at. */
const serviceName = (service: ServiceHealth) => service.service_name;

export function ServicesView({
  order,
  onOrderChange,
  filters,
  onFilterChange,
  onClearFilters,
  windowLabel,
  everReported,
  services,
  isPending,
  isError,
  error,
  onRetry,
  expanded,
  onToggleService,
  endpointsByService,
  trendByService,
  previousByService,
}: ServicesViewProps) {
  // Held by name, and by the *service* list rather than the row list: expanding a
  // service adds its endpoints, and those are not a ranking anybody is reaching into.
  const settled = useSettledOrder(services, serviceName, { frozen: expanded.size > 0 });
  const rows = buildServiceRows(settled.items, expanded, endpointsByService);
  const narrowed = appliedFilters(SERVICE_FILTERS, filters).length > 0;
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
            ? `Every service you send traces from, over ${windowLabel}.`
            : `${describeServices(services.length, windowLabel, order)} ${SEVERITY_LEGEND}`}
        </p>
      </header>

      <FilterBar
        definitions={SERVICE_FILTERS}
        values={filters}
        onChange={onFilterChange}
        onClearAll={onClearFilters}
      />

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={10} label="Loading services" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure title="Services could not be loaded" error={error} onRetry={onRetry} />
        </div>
      ) : services.length === 0 ? (
        <EmptyServices
          everReported={everReported}
          windowLabel={windowLabel}
          narrowed={narrowed}
          onClearFilters={onClearFilters}
        />
      ) : (
        <>
          {settled.reorderPending ? <ReorderNotice onApply={settled.applyOrder} /> : null}

          <ColumnHeader />

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto" {...settled.holdProps}>
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
                  previousByService={previousByService}
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
 * Why the list is empty, which is three different situations and not one.
 *
 * "Nothing matches these filters", "nothing has ever arrived" and "nothing arrived in
 * these five minutes" ask for opposite actions from the reader, and a screen that says
 * "no services" to all three sends two thirds of them to the wrong one.
 *
 * **`everReported` is a fact from the API, not a guess.** This used to read an empty
 * widest range as a project that had never reported, which told a customer whose
 * services had been quiet for eight days to go and instrument them again. The project
 * list now carries the first day anything arrived, read from the ingest meter, whose
 * rows no retention window expires.
 *
 * `undefined` — the list has not answered yet — is deliberately not "never". Offering
 * setup to somebody who has working instrumentation is the expensive mistake here;
 * offering a wider range to somebody with nothing costs a sentence.
 */
function EmptyServices({
  everReported,
  windowLabel,
  narrowed,
  onClearFilters,
}: {
  everReported: boolean | undefined;
  windowLabel: string;
  narrowed: boolean;
  onClearFilters: () => void;
}) {
  if (narrowed) {
    return (
      <EmptyState
        title="No services match these filters"
        detail={`Services reported in ${windowLabel}, but none of them match what you asked for. Drop a filter, or widen the time range.`}
        action={
          <Button variant="secondary" onClick={onClearFilters}>
            Clear all filters
          </Button>
        }
      />
    );
  }

  if (everReported === false) {
    return (
      <EmptyState
        title="No services yet"
        detail="Nothing has ever sent telemetry to this project. Point an OpenTelemetry SDK at your endpoint and the first service appears here within about a minute."
      />
    );
  }

  return (
    <EmptyState
      title={`No services in ${windowLabel}`}
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
      <span className={P95_COLUMN}>p95</span>
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
  previousByService,
  ceilings,
}: {
  row: ServiceRow;
  offsetTop: number;
  height: number;
  onToggleService: (serviceName: string) => void;
  endpointsByService: ReadonlyMap<string, EndpointGroup>;
  trendByService?: ReadonlyMap<string, readonly TrendPoint[]>;
  previousByService?: ReadonlyMap<string, ServiceHealth>;
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
          previous={previousByService?.get(row.service.service_name)}
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
  previous,
  ceilings,
}: {
  service: ServiceHealth;
  expanded: boolean;
  onToggle: () => void;
  trend?: readonly TrendPoint[];
  previous?: ServiceHealth;
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
        <SeverityMark severity={serviceSeverity(service)} />
        <span className="truncate text-ink">{service.service_name}</span>
      </span>
      <MetricCells metrics={service} previous={previous} />
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
 * **Two tiers now, and the thresholds are stated on the screen** — see `ColumnHeader`.
 * It was binary until 2026-09-14 because no threshold existed anywhere in the code and
 * inventing one inside a component would have decided product behaviour where nobody
 * could find it. The numbers live in `severity.ts` as one named pair, which is where to
 * change them.
 *
 * **Shape as well as hue**, which `design-system.md` asks for: a reader who cannot
 * separate amber from red still separates a ring from a disc.
 */
function SeverityMark({ severity }: { severity: ServiceSeverity }) {
  if (severity === "healthy") return <span className={MARK_COLUMN} aria-hidden />;

  return (
    <span className={MARK_COLUMN} aria-hidden>
      {severity === "critical" ? (
        <span data-severity="critical" className="size-1.75 rounded-full bg-critical-solid" />
      ) : (
        <span
          data-severity="warning"
          className="size-1.75 rounded-full border-[1.5px] border-warning-solid"
        />
      )}
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

/**
 * The numbers, in the same columns whether the row is a service or an endpoint.
 *
 * Three of them can carry a change against the previous window, and most rows
 * carry none: `deltaOf` is silent unless the move is worth an eye. The three are
 * the ones a person reads this screen for — how much traffic, how much of it
 * failed, and how slow the tail is — and each says which direction is the bad
 * one, because a rising request rate and a rising error rate are not the same
 * news.
 *
 * The error rate is compared in points rather than relatively: 0.1% becoming
 * 0.2% has doubled and means nothing, and 0% becoming 2% is a change no
 * percentage can express.
 */
function MetricCells({ metrics, previous }: { metrics: HealthMetrics; previous?: HealthMetrics }) {
  const failing = metrics.error_count > 0;
  const samples = previous && { current: metrics.request_count, previous: previous.request_count };

  return (
    <>
      <span
        className={`${RATE_COLUMN} font-mono text-2xs text-ink-dim`}
        title={describeVolume(metrics.request_count)}
      >
        <Changed value={metrics.requests_per_minute}>
          {formatRatePerMinute(metrics.requests_per_minute)}
        </Changed>
        <Delta
          delta={deltaOf("rate", metrics.requests_per_minute, previous?.requests_per_minute)}
          worse="down"
          label="Request rate"
        />
      </span>
      <span
        className={`${ERRORS_COLUMN} font-mono text-2xs ${
          failing ? "text-critical-text" : "text-ink-dim"
        }`}
      >
        <Changed value={metrics.error_count}>
          {formatErrors(metrics.error_count, metrics.error_rate)}
        </Changed>
        <Delta
          delta={deltaOf("ratio", metrics.error_rate, previous?.error_rate, samples)}
          worse="up"
          label="Error rate"
        />
      </span>
      <span className={`${LATENCY_COLUMN} ${WIDE_ONLY} font-mono text-2xs text-ink-dim`}>
        {formatDuration(metrics.p50_ms)}
      </span>
      {/* p95 is the one the ranking and the toggle are about, so it is the one that
          reads as text rather than as metadata. */}
      <span className={`${P95_COLUMN} font-mono text-2xs text-ink`}>
        {formatDuration(metrics.p95_ms)}
        <Delta
          delta={deltaOf("duration", metrics.p95_ms, previous?.p95_ms, samples)}
          worse="up"
          label="p95"
        />
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

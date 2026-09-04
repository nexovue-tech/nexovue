import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronRight } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows, SkeletonText } from "@/shared/components/Skeleton";
import { formatDuration } from "@/shared/format/duration";
import { formatErrors } from "@/shared/format/health";
import { formatRatePerMinute } from "@/shared/format/rate";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type { EndpointGroup, EndpointHealth, HealthMetrics, ServiceHealth } from "./api";
import { useEndpointsByService, useServiceList } from "./api";
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
      services={services.data?.services ?? []}
      isPending={services.isPending}
      isError={services.isError}
      error={services.error}
      onRetry={() => void services.refetch()}
      expanded={expanded}
      onToggleService={toggleService}
      endpointsByService={endpointsByService}
    />
  );
}

interface ServicesViewProps {
  order: ServiceOrder;
  onOrderChange: (order: ServiceOrder) => void;
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  services: readonly ServiceHealth[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  expanded: ReadonlySet<string>;
  onToggleService: (serviceName: string) => void;
  endpointsByService: ReadonlyMap<string, EndpointGroup>;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function ServicesView({
  order,
  onOrderChange,
  windowLabel,
  services,
  isPending,
  isError,
  error,
  onRetry,
  expanded,
  onToggleService,
  endpointsByService,
}: ServicesViewProps) {
  const rows = buildServiceRows(services, order, expanded, endpointsByService);

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
        <EmptyState
          title="No services in this window"
          detail="Nothing has sent a trace in the selected period. Widen the time range, or check that your services are still exporting — a service that stops reporting disappears from this list rather than turning red."
        />
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
    </div>
  );
}

function VirtualRow({
  row,
  offsetTop,
  height,
  onToggleService,
  endpointsByService,
}: {
  row: ServiceRow;
  offsetTop: number;
  height: number;
  onToggleService: (serviceName: string) => void;
  endpointsByService: ReadonlyMap<string, EndpointGroup>;
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
}: {
  service: ServiceHealth;
  expanded: boolean;
  onToggle: () => void;
}) {
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
        <span className="truncate text-ink">{service.service_name}</span>
      </span>
      <MetricCells metrics={service} />
    </button>
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

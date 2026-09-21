import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { X } from "lucide-react";
import { useMemo, useRef } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatDuration } from "@/shared/format/duration";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";

import type { TraceSummary } from "./api";
import { traceExportUrl, traceFailed, useTraceFacets, useTraceList } from "./api";
import type { ActiveFilter, TraceFilters, TraceStatusFilter } from "./filters";
import {
  activeFilters,
  clearFilter,
  explainEmptyResult,
  hasAnyFilter,
  NO_FILTERS,
  parseTraceFilters,
  toSearchParams,
} from "./filters";

const ROW_HEIGHT = 36;

/* Reserved on every row so the columns beside it do not shift when one trace
   has no bar to draw. */
const DURATION_BAR_COLUMN = "hidden w-24 shrink-0 pl-3 sm:block";
const OVERSCAN_ROWS = 10;

/** The two most useful latency questions, as one control rather than a number pad. */
const DURATION_FLOORS = [
  { id: "", label: "Any", ms: undefined },
  { id: "100", label: "over 100 ms", ms: 100 },
  { id: "500", label: "over 500 ms", ms: 500 },
  { id: "1000", label: "over 1 s", ms: 1000 },
  { id: "5000", label: "over 5 s", ms: 5000 },
] as const;

const STATUS_OPTIONS = [
  { id: "", label: "Any" },
  { id: "ERROR", label: "Failed" },
  { id: "OK", label: "No failures" },
] as const;

/**
 * Every assembled trace in the window, newest first, narrowed by whatever the URL says.
 *
 * One line each, no cards: service and name carry the identity, the numbers sit in
 * aligned columns so the eye scans down them, and the only colour on the screen marks
 * the traces that contain a failure.
 *
 * The filters live in the address bar rather than in this component, so the answer to
 * "which trace was it?" is a link somebody can paste to a colleague.
 */
export function TraceListScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();
  const navigate = useNavigate();

  // The route's own id, not its path: `/traces` renders inside the pathless shell.
  const search = useSearch({ from: "/shell/traces" });
  const filters = useMemo(() => parseTraceFilters(search), [search]);

  const list = useTraceList(projectId, timeWindow, filters);
  const facets = useTraceFacets(projectId, timeWindow);

  // `replace`, not push: the back button belongs to the screen a person came
  // from, not to every dropdown they tried on the way.
  const setFilters = (next: TraceFilters) =>
    void navigate({ to: "/traces", search: () => toSearchParams(next), replace: true });

  const traces = list.data?.traces ?? [];
  const ceiling = slowestDuration(traces);
  const services = facets.data?.services.map((facet) => facet.service_name) ?? [];
  const operations = useMemo(
    () => operationsFor(facets.data?.operations ?? [], filters.service),
    [facets.data, filters.service],
  );

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: traces.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    getItemKey: (index) => traces[index].trace_id,
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Traces</h1>
        <p className="mt-1 text-ink-dim text-sm">
          Every request assembled in {windowPhrase}, newest first.
        </p>
        {/* The filters travel with it, so the file answers the question on screen
            rather than the whole window. */}
        <a
          href={traceExportUrl(projectId ?? "", timeWindow, filters)}
          className="mt-1 inline-block text-ink-dim text-xs underline underline-offset-4 hover:text-ink"
        >
          Download these as CSV
        </a>
      </header>

      <FilterBar
        filters={filters}
        services={services}
        operations={operations}
        onChange={setFilters}
      />

      {list.isPending ? (
        <div className="px-4">
          <SkeletonRows rows={12} label="Loading traces" />
        </div>
      ) : list.isError ? (
        <div className="px-4">
          <InlineFailure
            title="Traces could not be loaded"
            error={list.error}
            onRetry={() => list.refetch()}
          />
        </div>
      ) : traces.length === 0 ? (
        <EmptyState
          title={
            hasAnyFilter(filters) ? "No traces match these filters" : "No traces in this window"
          }
          detail={explainEmptyResult(filters, windowPhrase, {
            services,
            operations: operations.map((facet) => facet.operation),
          })}
          action={
            hasAnyFilter(filters) ? (
              <Button variant="secondary" onClick={() => setFilters(NO_FILTERS)}>
                Clear all filters
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <div className="flex shrink-0 items-center border-line border-b bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
            <span className="min-w-0 flex-1">Request</span>
            <span className="hidden w-24 text-right sm:block">Spans</span>
            <span className="w-20 text-right">Took</span>
            {/* The bar's own column. Unlabelled — it draws "Took" — but present,
                because without it every heading to its right was a column out
                (`docs/quality/console-review-2026-09-20.md`, D7). */}
            <span aria-hidden="true" className={DURATION_BAR_COLUMN} />
            <span className="w-20 text-right sm:w-24">When</span>
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
            <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((item) => (
                <TraceRow
                  key={item.key}
                  trace={traces[item.index]}
                  offsetTop={item.start}
                  height={ROW_HEIGHT}
                  ceiling={ceiling}
                />
              ))}
            </div>

            {list.hasNextPage ? (
              <div className="flex justify-center border-line-subtle border-t py-4">
                <Button
                  variant="secondary"
                  pending={list.isFetchingNextPage}
                  pendingLabel="Loading…"
                  onClick={() => list.fetchNextPage()}
                >
                  Load older traces
                </Button>
              </div>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * The operations offered, narrowed to the chosen service.
 *
 * `GET /` from four services is four rows of the same word otherwise, and picking the
 * wrong one silently returns nothing.
 */
function operationsFor(
  operations: readonly { service_name: string; operation: string }[],
  service: string | undefined,
): { service_name: string; operation: string }[] {
  const relevant = service
    ? operations.filter((facet) => facet.service_name === service)
    : operations;

  const seen = new Set<string>();
  return relevant.filter((facet) => {
    if (seen.has(facet.operation)) return false;
    seen.add(facet.operation);
    return true;
  });
}

function FilterBar({
  filters,
  services,
  operations,
  onChange,
}: {
  filters: TraceFilters;
  services: readonly string[];
  operations: readonly { operation: string }[];
  onChange: (next: TraceFilters) => void;
}) {
  const applied = activeFilters(filters);

  return (
    <div className="shrink-0 px-4 pb-3">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
        {/* Native selects, deliberately: keyboard navigation, typeahead and the mobile
            picker all keep working, and there is nothing here a listbox would add. */}
        <InlineSelect
          label="Service"
          value={filters.service ?? ""}
          onChange={(value) =>
            // The operation belonged to the old service; keeping it would filter
            // on a pair that never existed and return nothing.
            onChange({ ...filters, service: value || undefined, operation: undefined })
          }
          options={optionsFor(services, filters.service)}
        />

        <InlineSelect
          label="Operation"
          value={filters.operation ?? ""}
          onChange={(value) => onChange({ ...filters, operation: value || undefined })}
          options={optionsFor(
            operations.map((facet) => facet.operation),
            filters.operation,
          )}
        />

        <InlineSelect
          label="Status"
          value={filters.status ?? ""}
          onChange={(value) =>
            onChange({ ...filters, status: (value || undefined) as TraceStatusFilter | undefined })
          }
          options={STATUS_OPTIONS.map((option) => ({ value: option.id, label: option.label }))}
        />

        <InlineSelect
          label="Took"
          value={filters.minDurationMs === undefined ? "" : String(filters.minDurationMs)}
          onChange={(value) =>
            onChange({ ...filters, minDurationMs: value === "" ? undefined : Number(value) })
          }
          options={DURATION_FLOORS.map((floor) => ({ value: floor.id, label: floor.label }))}
        />

        <AttributeFilter filters={filters} onChange={onChange} />
      </div>

      {applied.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {applied.map((filter) => (
            <FilterChip
              key={filter.key}
              filter={filter}
              onClear={() => onChange(clearFilter(filters, filter.key))}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The escape hatch: any span attribute, by name.
 *
 * Typed rather than chosen, and that is the point — one hour of a twelve-service
 * deployment produced 125 distinct attribute names, so there is no list to offer.
 * It only becomes a filter once both halves are filled, which is why it submits on
 * a form rather than on each keystroke.
 */
function AttributeFilter({
  filters,
  onChange,
}: {
  filters: TraceFilters;
  onChange: (next: TraceFilters) => void;
}) {
  return (
    <form
      // Wrapping, and the inputs free to shrink: at 390px this row used to put
      // Apply 22px past the edge of a page that cannot scroll sideways, so the
      // filter could not be applied at all
      // (`docs/quality/console-review-2026-09-20.md`, D3).
      className="flex flex-wrap items-baseline gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const attr = String(form.get("attr") ?? "").trim();
        const attrValue = String(form.get("attr_value") ?? "").trim();
        onChange({
          ...filters,
          attr: attr && attrValue ? attr : undefined,
          attrValue: attr && attrValue ? attrValue : undefined,
        });
      }}
    >
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Attribute</span>
      <input
        name="attr"
        defaultValue={filters.attr ?? ""}
        placeholder="db.system"
        aria-label="Span attribute name"
        className="w-32 min-w-24 flex-1 rounded-sm border border-line bg-surface px-2 py-0.5 font-mono text-ink text-xs outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      />
      <input
        name="attr_value"
        defaultValue={filters.attrValue ?? ""}
        placeholder="postgresql"
        aria-label="Span attribute value"
        className="w-32 min-w-24 flex-1 rounded-sm border border-line bg-surface px-2 py-0.5 font-mono text-ink text-xs outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      />
      <button
        type="submit"
        className="rounded-sm border border-line bg-raised px-1.5 py-0.5 font-mono text-2xs text-ink uppercase tracking-caps hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      >
        Apply
      </button>
    </form>
  );
}

/** What is currently narrowing the list, and the way out of each one. */
function FilterChip({ filter, onClear }: { filter: ActiveFilter; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-line bg-raised py-0.5 pr-1 pl-2 text-2xs">
      <span className="text-ink-dim">{filter.label}</span>
      <span className="font-mono text-ink">{filter.value}</span>
      <button
        type="button"
        onClick={onClear}
        aria-label={`Remove the ${filter.label.toLowerCase()} filter`}
        className="rounded-sm p-0.5 text-ink-dim hover:bg-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      >
        <X size={11} aria-hidden="true" />
      </button>
    </span>
  );
}

/**
 * What the window holds, plus whatever is already selected.
 *
 * A shared link can name a service that has since gone quiet. Without it in the
 * list a native select silently shows "All" while the filter is still applied —
 * the control saying one thing and the results another.
 */
function optionsFor(
  known: readonly string[],
  selected: string | undefined,
): { value: string; label: string }[] {
  const names = selected && !known.includes(selected) ? [selected, ...known] : known;
  return [{ value: "", label: "All" }, ...names.map((name) => ({ value: name, label: name }))];
}

function InlineSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly { value: string; label: string }[];
}) {
  return (
    <label className="flex items-baseline gap-2">
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="max-w-44 cursor-pointer appearance-none truncate rounded-sm border-none bg-transparent py-0.5 pr-1 pl-1 font-medium text-ink text-sm outline-none hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * The slowest trace on the page, which every bar is drawn against.
 *
 * One ceiling for the whole list, never per row: a bar that autoscaled to its
 * own value would be full width on every row, and a list where everything is
 * full width says nothing at all. This is the same rule `Sparkline` follows,
 * and for the same reason.
 */
export function slowestDuration(traces: readonly TraceSummary[]): number {
  return traces.reduce((slowest, trace) => Math.max(slowest, trace.duration_ms), 0);
}

/** The width, as a fraction. A trace at the ceiling fills the cell. */
export function barFraction(durationMs: number, ceiling: number): number {
  if (!Number.isFinite(durationMs) || durationMs <= 0 || ceiling <= 0) return 0;
  return Math.min(1, durationMs / ceiling);
}

function DurationBar({
  durationMs,
  ceiling,
  failed,
}: {
  durationMs: number;
  ceiling: number;
  failed: boolean;
}) {
  const fraction = barFraction(durationMs, ceiling);
  // Nothing to compare it against, so nothing is drawn. A zero-width bar on a
  // single-trace page would read as "instant" rather than as "no scale yet".
  if (fraction === 0) return null;

  return (
    <span
      className="block h-1 rounded-xs bg-line"
      // `aria-hidden`: the duration is already in the cell beside this, as a
      // number. A second reading of the same value is noise to a screen reader.
      aria-hidden="true"
    >
      <span
        className={`block h-full rounded-xs ${failed ? "bg-critical-solid" : "bg-brand"}`}
        style={{ width: `${fraction * 100}%` }}
      />
    </span>
  );
}

function TraceRow({
  trace,
  offsetTop,
  height,
  ceiling,
}: {
  trace: TraceSummary;
  offsetTop: number;
  height: number;
  ceiling: number;
}) {
  const failed = traceFailed(trace);

  return (
    <div
      className="absolute inset-x-0 top-0"
      style={{ height, transform: `translateY(${offsetTop}px)` }}
    >
      <Link
        to="/traces/$traceId"
        params={{ traceId: trace.trace_id }}
        className={`flex h-full items-center border-line-subtle border-b border-l-2 px-4 text-sm hover:bg-hover ${
          failed ? "border-l-critical-solid" : "border-l-transparent"
        }`}
      >
        <span className="flex min-w-0 flex-1 items-baseline gap-2">
          {/* The name identifies the request; the service is the first thing to go when
              there is no room for both, and the trace screen names it immediately. */}
          <span className="hidden shrink-0 font-mono text-2xs text-ink-dim sm:inline">
            {trace.root_service}
          </span>
          <span className="truncate text-ink">{trace.root_name}</span>
        </span>

        <span className="hidden w-24 shrink-0 text-right font-mono text-2xs text-ink-dim sm:block">
          {trace.span_count}
          {trace.error_count > 0 ? (
            <span className="text-critical-text"> · {trace.error_count} failed</span>
          ) : null}
        </span>

        <span className="w-20 shrink-0 text-right font-mono text-2xs text-ink-dim">
          {formatDuration(trace.duration_ms)}
        </span>

        <span className={DURATION_BAR_COLUMN}>
          <DurationBar durationMs={trace.duration_ms} ceiling={ceiling} failed={failed} />
        </span>

        <span
          className="w-20 shrink-0 text-right font-mono text-2xs text-ink-dim sm:w-24"
          title={formatTimestamp(trace.start_time)}
        >
          {formatRelativeTime(trace.start_time)}
        </span>
      </Link>
    </div>
  );
}

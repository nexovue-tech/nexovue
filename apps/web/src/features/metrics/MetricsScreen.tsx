import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import type { FilterValues } from "@/shared/components/FilterBar";
import { FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { filterParams, withFilter } from "@/shared/filters";
import { formatBound } from "@/shared/format/metric-unit";
import { formatCount, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";
import type { HistogramLayout, HistogramResponse, MetricSummary } from "./api";
import { useHistogram, useMetricCatalogue } from "./api";

import { estimateReadings, histogramBars, meanOf } from "./buckets";
import { METRIC_FILTERS, METRIC_NAME_KEY, metricFilters, metricsNarrowed } from "./filters";
import { HistogramChart } from "./HistogramChart";

/** Typing a character must not send a request; the pause before the next one may. */
const NAME_DEBOUNCE_MS = 300;

/**
 * What this project actually reports, and the distribution behind one of them.
 *
 * **The screen exists because every other one reads a metric we chose in advance.** A
 * customer who instruments a queue depth, a cache hit rate, a job duration had it
 * stored, masked and retained, and could see none of it. The catalogue answers "what is
 * in here"; the histogram answers "and what does it look like".
 *
 * A metric whose shape has no reader is listed with its counts and says so, rather than
 * being hidden. Hiding it would answer the screen's own question wrongly: it *is* in
 * here, and a customer paying to store it should be told that before being told we
 * cannot draw it yet.
 *
 * All three filters go to the server, because the catalogue is capped: see
 * `filters.ts`.
 */
export function MetricsScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow();
  const navigate = useNavigate();

  const search = useSearch({ from: "/shell/metrics" });
  const selected = typeof search.metric === "string" ? search.metric : undefined;
  const filters = useMemo(() => metricFilters(search), [search]);

  // `replace`: picking through a catalogue is browsing, and the back button belongs
  // to the screen somebody arrived from rather than to every name they tried.
  // The chosen metric and the filters travel together — narrowing the list is not
  // a reason to close the distribution somebody is reading.
  const show = (next: FilterValues, metricName = selected) =>
    void navigate({
      to: "/metrics",
      search: () => ({ ...filterParams(next), ...(metricName ? { metric: metricName } : {}) }),
      replace: true,
    });

  const [typedName, onTypedName] = useTypedName(filters[METRIC_NAME_KEY] ?? "", (name) =>
    show(withFilter(filters, METRIC_NAME_KEY, name)),
  );

  const catalogue = useMetricCatalogue(projectId, timeWindow, filterParams(filters));
  const histogram = useHistogram(projectId, selected, timeWindow);

  return (
    <MetricsView
      windowLabel={timeRangeById(rangeId).label}
      metrics={catalogue.data?.metrics ?? []}
      truncated={catalogue.data?.truncated ?? false}
      isPending={catalogue.isPending}
      isError={catalogue.isError}
      error={catalogue.error}
      onRetry={() => void catalogue.refetch()}
      selected={selected}
      onSelect={(metricName) => show(filters, metricName)}
      filters={filters}
      typedName={typedName}
      onTypedName={onTypedName}
      onFilterChange={(key, value) => show(withFilter(filters, key, value))}
      onClearFilters={() => show({})}
      histogram={histogram.data}
      histogramPending={histogram.isFetching}
      histogramError={histogram.isError}
    />
  );
}

/**
 * The name being typed, handed to the URL once typing stops.
 *
 * **Held here and not read back out of the address bar.** The router applies a
 * navigation inside a React transition, and a text input rendered from state a
 * transition owns shows the value it had before the keystroke — fast typing
 * loses characters. The box is local; the filter is still the URL's, arriving a
 * beat later. That beat is also what keeps a keystroke from becoming a
 * group-by across five metric tables.
 */
function useTypedName(
  urlName: string,
  onSettled: (name: string | undefined) => void,
): [string, (value: string) => void] {
  const [typed, setTyped] = useState(urlName);
  const [knownUrlName, setKnownUrlName] = useState(urlName);
  const settled = useDebounced(typed, NAME_DEBOUNCE_MS);
  const sent = useRef(urlName);

  // Held in a ref and refreshed first, so the settling below calls the newest
  // one: it closes over the other filters, and a shape chosen mid-word must not
  // be undone by a callback captured before it.
  const commit = useRef(onSettled);
  useEffect(() => {
    commit.current = onSettled;
  });

  // The URL changed under the box — cleared from the chip row, or a link that
  // arrived already narrowed. Adjusted while rendering rather than in an effect,
  // which would paint the stale value first.
  if (urlName !== knownUrlName) {
    setKnownUrlName(urlName);
    setTyped(urlName);
    sent.current = urlName;
  }

  useEffect(() => {
    if (settled === sent.current) return;
    sent.current = settled;
    commit.current(settled || undefined);
  }, [settled]);

  return [typed, setTyped];
}

/** Holds the last value still, so a keystroke does not become a request. */
function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}

export interface MetricsViewProps {
  windowLabel: string;
  metrics: readonly MetricSummary[];
  truncated: boolean;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  selected?: string;
  onSelect: (metricName: string | undefined) => void;
  filters: FilterValues;
  /** What is in the search box, which leads the URL by one debounce. */
  typedName: string;
  onTypedName: (value: string) => void;
  onFilterChange: (key: string, value: string | undefined) => void;
  onClearFilters: () => void;
  histogram?: HistogramResponse;
  histogramPending: boolean;
  histogramError: boolean;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function MetricsView({
  windowLabel,
  metrics,
  truncated,
  isPending,
  isError,
  error,
  onRetry,
  selected,
  onSelect,
  filters,
  typedName,
  onTypedName,
  onFilterChange,
  onClearFilters,
  histogram,
  histogramPending,
  histogramError,
}: MetricsViewProps) {
  // The typed name counts as narrowing the moment it is typed, not when it
  // reaches the URL: otherwise the empty state reached by typing blames the
  // project for a filter the reader can see in the box.
  const narrowed = metricsNarrowed(filters) || typedName !== "";

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Metrics</h1>
        <p className="mt-1 text-ink-dim text-sm">
          {narrowed
            ? `The metrics matching these filters, of everything reported over the last ${windowLabel}.`
            : `Every metric this project reported over the last ${windowLabel}, busiest first.`}
        </p>
      </header>

      {/* Its own row above the declared controls: `FilterBar` renders a select or
          a toggle, and three hundred names are typed at rather than picked from. */}
      <div className="shrink-0 px-4 pb-2">
        <input
          type="search"
          value={typedName}
          onChange={(event) => onTypedName(event.target.value)}
          placeholder="Search metric names"
          aria-label="Search metric names"
          className="w-full max-w-sm rounded-sm border border-line bg-surface px-2.5 py-1 font-mono text-ink text-xs outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
        />
      </div>

      <FilterBar
        definitions={METRIC_FILTERS}
        values={filters}
        onChange={onFilterChange}
        onClearAll={onClearFilters}
      />

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={8} label="Loading the metric catalogue" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure
            title="The metric catalogue could not be loaded"
            error={error}
            onRetry={onRetry}
          />
        </div>
      ) : metrics.length === 0 ? (
        <EmptyCatalogue narrowed={narrowed} onClearFilters={onClearFilters} />
      ) : (
        <>
          {selected ? (
            <SelectedMetric
              metricName={selected}
              histogram={histogram}
              isPending={histogramPending}
              isError={histogramError}
              onClear={() => onSelect(undefined)}
            />
          ) : null}

          <Catalogue metrics={metrics} selected={selected} onSelect={onSelect} />

          {/* The cap applies after the filters, so a narrowed list that was still
              cut was cut on what matched — saying "this project reports more"
              there would blame the project for the filter's own ceiling. */}
          {truncated ? (
            <p className="shrink-0 px-4 py-3 text-ink-dim text-xs">
              {narrowed
                ? "More metric names match these filters than this list holds. The busiest are shown."
                : "This project reports more metric names than this list holds. The busiest are shown."}
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

/**
 * Why the catalogue is empty, which is two situations and not one.
 *
 * "Nothing matched what you asked for" and "this project has never reported a
 * metric" ask opposite things of the reader, and one sentence for both sends
 * half of them to instrument a service that is already instrumented.
 */
function EmptyCatalogue({
  narrowed,
  onClearFilters,
}: {
  narrowed: boolean;
  onClearFilters: () => void;
}) {
  if (narrowed) {
    return (
      <EmptyState
        title="No metrics match these filters"
        detail="Nothing this project reported over this period matches what you asked for. Drop a filter, or widen the time range."
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
      title="No metrics in this window"
      detail="Nothing in this project sent a metric over this period. An OpenTelemetry SDK exports metrics separately from traces, so a service can be tracing happily and reporting no metrics at all — or widen the time range if you expected something older."
    />
  );
}

function Catalogue({
  metrics,
  selected,
  onSelect,
}: {
  metrics: readonly MetricSummary[];
  selected?: string;
  onSelect: (metricName: string | undefined) => void;
}) {
  return (
    <section className="shrink-0 border-line border-t">
      <div className="flex items-baseline justify-between px-4 py-2">
        <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {formatCount(metrics.length)} {plural(metrics.length, "metric")}
        </h2>
      </div>

      {/* A number with no column heading is not a reading. Four of them in a row is
          a table nobody can use, however correct the numbers are. */}
      <div className="flex items-center gap-3 border-line-subtle border-b px-4 pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="min-w-0 flex-1">Metric</span>
        <span className="w-40 shrink-0 text-right">Shape</span>
        <span className="w-16 shrink-0 text-right">Unit</span>
        <span className="w-20 shrink-0 text-right">Series</span>
        <span className="w-20 shrink-0 text-right">Points</span>
        <span className="w-24 shrink-0 text-right" />
      </div>

      <ul>
        {metrics.map((metric) => (
          <li key={metric.metric_name}>
            <MetricRow
              metric={metric}
              isSelected={metric.metric_name === selected}
              onSelect={onSelect}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function MetricRow({
  metric,
  isSelected,
  onSelect,
}: {
  metric: MetricSummary;
  isSelected: boolean;
  onSelect: (metricName: string | undefined) => void;
}) {
  const shapes = metric.shapes.join(", ") || UNKNOWN;
  const conflicted = metric.shapes.length > 1;

  return (
    <button
      type="button"
      // A metric we cannot draw is still a row worth reading, so the button stays
      // enabled and the detail panel is what explains itself. Disabling it would
      // make "why can I not click this?" the reader's question instead of ours.
      onClick={() => onSelect(metric.readable ? metric.metric_name : undefined)}
      aria-pressed={isSelected}
      disabled={!metric.readable}
      className={`flex w-full items-center gap-3 border-line-subtle border-b px-4 py-1.5 text-left text-sm ${
        isSelected ? "bg-hover" : ""
      } ${metric.readable ? "hover:bg-hover" : "cursor-default"}`}
    >
      <span className="min-w-0 flex-1 truncate text-ink">{metric.metric_name}</span>

      <span
        className={`w-40 shrink-0 truncate text-right font-mono text-2xs ${
          conflicted ? "text-warning-text" : "text-ink-dim"
        }`}
      >
        {shapes}
      </span>
      <span className="w-16 shrink-0 text-right font-mono text-2xs text-ink-dim">
        {metric.unit || UNKNOWN}
      </span>
      <span className="w-20 shrink-0 text-right font-mono text-2xs text-ink-dim">
        {formatCount(metric.series)}
      </span>
      <span className="w-20 shrink-0 text-right font-mono text-2xs text-ink-dim">
        {formatCount(metric.points)}
      </span>
      <span className="w-24 shrink-0 text-right font-mono text-2xs text-ink-dim">
        {metric.readable ? "" : "no chart yet"}
      </span>
    </button>
  );
}

function SelectedMetric({
  metricName,
  histogram,
  isPending,
  isError,
  onClear,
}: {
  metricName: string;
  histogram?: HistogramResponse;
  isPending: boolean;
  isError: boolean;
  onClear: () => void;
}) {
  return (
    <section className="shrink-0 border-line border-b px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="min-w-0 truncate font-mono text-ink text-sm">{metricName}</h2>
        <button
          type="button"
          onClick={onClear}
          className="shrink-0 text-brand-text text-sm underline-offset-2 hover:underline"
        >
          Close
        </button>
      </div>

      {histogram?.description ? (
        <p className="mt-1 text-ink-dim text-sm">{histogram.description}</p>
      ) : null}

      {isPending ? (
        <div className="mt-3">
          <SkeletonRows rows={3} label={`Loading the distribution for ${metricName}`} />
        </div>
      ) : isError ? (
        <p className="mt-3 text-critical-text text-sm">
          The distribution for this metric could not be loaded.
        </p>
      ) : !histogram || histogram.layouts.length === 0 ? (
        <p className="mt-3 text-ink-dim text-sm">This metric reported nothing in this window.</p>
      ) : (
        <>
          {histogram.merged ? null : (
            <p className="mt-2 rounded-sm border border-warning-border bg-warning-subtle px-3 py-2 text-warning-text text-sm">
              This metric arrived with {formatCount(histogram.layouts.length)} different bucket
              layouts, so they are shown separately. Adding them together would combine
              distributions measured on different scales — the services below chose different
              boundaries for the same metric name.
            </p>
          )}

          {histogram.layouts.map((layout) => (
            <Layout
              key={layout.bounds.join(",") || "unbounded"}
              layout={layout}
              unit={histogram.unit}
              metricName={metricName}
            />
          ))}
        </>
      )}
    </section>
  );
}

function Layout({
  layout,
  unit,
  metricName,
}: {
  layout: HistogramLayout;
  unit: string;
  metricName: string;
}) {
  const bars = histogramBars(layout, unit);
  const mean = meanOf(layout);

  return (
    <div className="mt-3">
      {layout.services.length > 0 ? (
        <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {layout.services.join(", ")}
        </p>
      ) : null}

      <div className="mt-2">
        <HistogramChart
          bars={bars}
          label={`The distribution of ${metricName} across ${formatCount(bars.length)} buckets.`}
        />
      </div>

      {/* Deliberately *not* a five-column grid spanning the chart's width. Laid out
          that way the five estimates land at 0, 25, 50, 75 and 100% across the bars
          and read as marks on the axis — which would claim a position each one does
          not have. A packed row says they are readings, like the ones beside them. */}
      <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1">
        {estimateReadings(layout).map((estimate) => (
          <Reading
            key={estimate.label}
            term={estimate.label}
            value={formatBound(estimate.value, unit)}
          />
        ))}
      </dl>

      {/* Said once, near the numbers, rather than in a tooltip nobody opens. A
          percentile read off buckets is interpolated inside the bucket the rank
          falls in — as precise as the boundaries allow and no more. */}
      <p className="mt-1 text-ink-dim text-xs">
        Percentiles are estimates, interpolated from bucket boundaries.
      </p>

      <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1">
        <Reading term="Observations" value={formatCount(layout.count)} />
        <Reading term="Series" value={formatCount(layout.series)} />
        <Reading term="Mean" value={formatBound(mean, unit)} />
        <Reading term="Min" value={formatBound(layout.min, unit)} />
        <Reading term="Max" value={formatBound(layout.max, unit)} />
      </dl>
    </div>
  );
}

function Reading({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{term}</dt>
      <dd className="font-mono text-ink text-sm">{value}</dd>
    </div>
  );
}

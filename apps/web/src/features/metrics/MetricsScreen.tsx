import { useNavigate, useSearch } from "@tanstack/react-router";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatCount, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type { HistogramLayout, HistogramResponse, MetricSummary } from "./api";
import { useHistogram, useMetricCatalogue } from "./api";
import { estimateReadings, formatBound, histogramBars, meanOf } from "./buckets";
import { HistogramChart } from "./HistogramChart";

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
 */
export function MetricsScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow(rangeId);
  const navigate = useNavigate();

  const search = useSearch({ from: "/shell/metrics" });
  const selected = typeof search.metric === "string" ? search.metric : undefined;

  const catalogue = useMetricCatalogue(projectId, timeWindow);
  const histogram = useHistogram(projectId, selected, timeWindow);

  // `replace`: picking through a catalogue is browsing, and the back button belongs
  // to the screen somebody arrived from rather than to every name they tried.
  const select = (metricName: string | undefined) =>
    void navigate({
      to: "/metrics",
      search: () => (metricName ? { metric: metricName } : {}),
      replace: true,
    });

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
      onSelect={select}
      histogram={histogram.data}
      histogramPending={histogram.isFetching}
      histogramError={histogram.isError}
    />
  );
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
  histogram,
  histogramPending,
  histogramError,
}: MetricsViewProps) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Metrics</h1>
        <p className="mt-1 text-ink-dim text-sm">
          Every metric this project reported over the last {windowLabel}, busiest first.
        </p>
      </header>

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
        <EmptyState
          title="No metrics in this window"
          detail="Nothing in this project sent a metric over this period. An OpenTelemetry SDK exports metrics separately from traces, so a service can be tracing happily and reporting no metrics at all — or widen the time range if you expected something older."
        />
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

          {truncated ? (
            <p className="shrink-0 px-4 py-3 text-ink-dim text-xs">
              This project reports more metric names than this list holds. The busiest are shown.
            </p>
          ) : null}
        </>
      )}
    </div>
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

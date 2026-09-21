import { useCallback, useMemo, useState } from "react";

import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatDuration } from "@/shared/format/duration";
import { formatClockTime } from "@/shared/format/time";
import type { TimeWindow } from "@/shared/selection";

import type { CheckResultSummary, PhaseBucket } from "./api";
import { useCheckRuns, usePhaseBreakdown } from "./api";
import { PhaseChart } from "./PhaseChart";
import type { PhaseReading, SeriesToken } from "./phase-series";
import { buildPhasePlot, describeBreakdown } from "./phase-series";
import { RunStrip } from "./RunStrip";

/**
 * Where one check's time went — the reason this product's uptime checking is worth more
 * than a ping.
 *
 * "Your site is slow" and "your DNS is slow" are different incidents belonging to
 * different people, and the five phases are the whole difference between them. So the
 * chart is the largest thing on the screen, and the sentence above it says the answer
 * out loud before anyone reads an axis.
 */

const SERIES_MARK_CLASS: Record<SeriesToken, string> = {
  1: "bg-series-1",
  2: "bg-series-2",
  3: "bg-series-3",
  4: "bg-series-4",
  5: "bg-series-5",
  6: "bg-series-6",
};

export interface PhaseBreakdownPanelProps {
  projectId: string | undefined;
  check: CheckResultSummary;
  timeWindow: TimeWindow;
}

export function PhaseBreakdownPanel({ projectId, check, timeWindow }: PhaseBreakdownPanelProps) {
  const breakdown = usePhaseBreakdown(projectId, check.check_id, timeWindow);
  const runs = useCheckRuns(projectId, check.check_id, timeWindow);
  const [hoveredBucket, setHoveredBucket] = useState<number | null>(null);

  // Memoised because the chart is rebuilt whenever this object changes identity, and a
  // live window hands back a new response every tick even when nothing moved.
  const plot = useMemo(() => buildPhasePlot(breakdown.data), [breakdown.data]);
  const onHoverBucket = useCallback((index: number | null) => setHoveredBucket(index), []);

  if (breakdown.isPending) {
    return <SkeletonRows rows={5} label="Loading the phase breakdown" />;
  }

  if (breakdown.isError) {
    return (
      <InlineFailure
        title="The phase breakdown could not be loaded"
        error={breakdown.error}
        onRetry={() => breakdown.refetch()}
      />
    );
  }

  const bucket = hoveredBucketOf(breakdown.data?.buckets, hoveredBucket);

  return (
    <section className="border-line-subtle border-t px-4 py-5">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">
        Where the time goes
      </h2>
      <p className="mt-1 max-w-2xl text-ink-dim text-sm leading-relaxed">
        {describeBreakdown(plot.legend, plot.meanTotalMs, {
          runs: check.runs,
          successfulRuns: check.successful_runs,
        })}
      </p>

      {plot.bucketCount === 0 ? (
        <p className="mt-4 text-ink-dim text-sm">
          No phase readings landed in this window. Widen the time range, or check that the prober is
          running.
        </p>
      ) : (
        <div className="mt-4 flex flex-col gap-5 lg:flex-row lg:items-start">
          <div className="min-w-0 flex-1">
            <PhaseChart
              plot={plot}
              onHoverBucket={onHoverBucket}
              description={chartDescription(plot.legend, plot.meanTotalMs, plot.bucketCount)}
            />
          </div>

          <PhaseLegend legend={plot.legend} bucket={bucket} meanTotalMs={plot.meanTotalMs} />
        </div>
      )}

      <div className="mt-6">
        <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Recent probes</h3>
        {runs.isPending ? (
          <div className="mt-2">
            <SkeletonRows rows={1} label="Loading probes" />
          </div>
        ) : runs.isError ? (
          <div className="mt-2">
            <InlineFailure
              title="Probes could not be loaded"
              error={runs.error}
              onRetry={() => runs.refetch()}
            />
          </div>
        ) : (runs.data?.length ?? 0) === 0 ? (
          <p className="mt-2 text-ink-dim text-sm">No probes in this window.</p>
        ) : (
          <>
            <div className="mt-2">
              <RunStrip runs={runs.data ?? []} />
            </div>
            <p className="mt-2 font-mono text-2xs text-ink-dim">
              {runs.data?.length} probes · oldest left · height is duration
            </p>
          </>
        )}
      </div>
    </section>
  );
}

/**
 * The numbers beside the chart. It reads the window's means until the pointer picks a
 * bucket, at which point the same rows answer for that moment instead — which is the
 * whole reason to hover a chart, and cheaper than a floating tooltip.
 */
function PhaseLegend({
  legend,
  bucket,
  meanTotalMs,
}: {
  legend: readonly PhaseReading[];
  bucket: PhaseBucket | undefined;
  meanTotalMs: number;
}) {
  const total = bucket ? bucket.total_ms : meanTotalMs;

  return (
    <div className="w-full shrink-0 lg:w-64">
      <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {bucket ? formatClockTime(bucket.timestamp) : "Window mean"}
      </p>

      <ul className="mt-2">
        {legend.map((reading) => {
          const value = bucket ? (bucket.durations_ms[reading.phase] ?? 0) : reading.meanMs;
          return (
            <li
              key={reading.phase}
              className="flex items-baseline gap-2 border-line-subtle border-b py-1 text-sm last:border-b-0"
            >
              <span
                aria-hidden="true"
                className={`h-2 w-2 shrink-0 translate-y-px ${SERIES_MARK_CLASS[reading.seriesToken]}`}
              />
              <span className="min-w-0 flex-1 truncate text-ink">{reading.phase}</span>
              <span className="w-16 text-right font-mono text-2xs text-ink-dim">
                {formatDuration(value)}
              </span>
              <span className="w-12 text-right font-mono text-2xs text-ink-dim">
                {sharePercent(value, total)}
              </span>
            </li>
          );
        })}
      </ul>

      <p className="mt-2 flex items-baseline justify-between font-mono text-2xs text-ink-dim">
        <span className="uppercase tracking-caps">Total</span>
        <span className="text-ink">{formatDuration(total)}</span>
      </p>
    </div>
  );
}

function sharePercent(value: number, total: number): string {
  if (total <= 0) return "—";
  return `${Math.round((value / total) * 100)}%`;
}

function hoveredBucketOf(
  buckets: PhaseBucket[] | undefined,
  index: number | null,
): PhaseBucket | undefined {
  if (!buckets || index === null) return undefined;
  return buckets[index];
}

/** The sentence the chart is evidence for. */
/** What the canvas would say if it could be read. */
function chartDescription(
  legend: readonly PhaseReading[],
  meanTotalMs: number,
  bucketCount: number,
): string {
  const parts = legend.map((reading) => `${reading.phase} ${formatDuration(reading.meanMs)}`);
  return `Stacked request phases across ${bucketCount} buckets. Window means: ${parts.join(", ")}; total ${formatDuration(meanTotalMs)}.`;
}

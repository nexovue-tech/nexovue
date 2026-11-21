import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TrendChart } from "@/shared/components/TrendChart";

import { useSloBurn } from "./api";
import { chartDescription } from "./slo-words";

const LABELS = ["Times faster than allowed", "On pace (1)"] as const;

/**
 * *How fast* (design, *One target*, item 5): the long-window rate of the tier the status
 * names, by the minute over the last day, beside the on-pace line. Its point at the
 * job's minute is the stored rate (AC109); a minute with no requests is a gap. A failure
 * here leaves the figures above as they are.
 */
export function SloBurnChart({ sloId }: { sloId: string }) {
  const line = useSloBurn(sloId);
  return (
    <section aria-label="How fast" className="flex flex-col gap-2 text-sm">
      <h2 className="font-semibold text-ink">How fast</h2>
      {line.isPending ? (
        <SkeletonRows rows={2} label="Reading how fast" />
      ) : line.isError ? (
        <InlineFailure
          title="How fast could not be read"
          error={line.error}
          onRetry={() => void line.refetch()}
        />
      ) : line.data.tier === null ? (
        <p className="max-w-prose text-ink-dim">
          No tier can judge this target yet, so there is no line to draw.
        </p>
      ) : (
        <TrendChart
          label={LABELS}
          seriesToken={1}
          data={[
            line.data.points.map((point) => Date.parse(point.minute) / 1000),
            line.data.points.map((point) => point.rate),
            line.data.points.map(() => line.data.on_pace),
          ]}
          formatValue={(value) => `${Math.round(value * 10) / 10}×`}
          description={chartDescription(line.data)}
        />
      )}
    </section>
  );
}

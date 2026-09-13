import { formatBound } from "@/shared/format/metric-unit";
import { formatCount } from "@/shared/format/number";

/**
 * A latency distribution over time: buckets down, time across, density as colour.
 *
 * **Drawn only where a customer sends OTLP histograms** (OD7). `latency_quantiles` keeps
 * three quantiles and cannot reconstruct a distribution, and `spans.duration_ms` is
 * post-sampling, so this panel can exist only where a real histogram does. The caller is
 * told whether it has one, and leaves the panel out rather than drawing an empty grid —
 * an empty heatmap and a service with no traffic are the same picture.
 *
 * **Colour is per-column, not per-grid.** A heatmap scaled to its busiest cell across the
 * whole window shows one bright band at peak traffic and near-black everywhere else,
 * which answers "when was it busy" — a question the rate chart above already answers
 * better. Scaling each column to its own total makes every column a *shape*, so a tail
 * that grows at 3am is visible even though 3am is quiet. That is the reading this panel
 * exists for.
 */

const ROW_HEIGHT = 14;
/** Enough that a bucket holding anything at all is visible against the ground. */
const MINIMUM_SHADE = 0.12;

export interface HeatmapColumn {
  bucket: string;
  counts: readonly number[];
  total: number;
}

interface DurationHeatmapProps {
  bounds: readonly number[];
  columns: readonly HeatmapColumn[];
  unit: string;
  /** Read in place of the grid, which says nothing on its own. */
  label: string;
}

export function DurationHeatmap({ bounds, columns, unit, label }: DurationHeatmapProps) {
  if (columns.length === 0 || bounds.length === 0) return null;

  // One more row than there are bounds: the last holds everything above the last
  // boundary, and it is the row a reader looking for a tail goes to first.
  const rows = bounds.length + 1;
  const height = rows * ROW_HEIGHT;
  const columnWidth = 100 / columns.length;

  return (
    <div className="flex gap-2">
      <svg
        role="img"
        aria-label={label}
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        className="block w-full"
        style={{ height }}
      >
        <title>{label}</title>
        {columns.map((column, columnIndex) =>
          column.counts.map((count, rowIndex) => {
            const share = column.total > 0 ? count / column.total : 0;
            // Row 0 is the fastest bucket and sits at the bottom, so the tail
            // grows upward the way every latency chart a reader has seen does.
            const y = height - (rowIndex + 1) * ROW_HEIGHT;
            return (
              <rect
                key={`${column.bucket}-${bounds[rowIndex] ?? "over"}`}
                x={columnIndex * columnWidth}
                y={y}
                width={columnWidth}
                height={ROW_HEIGHT}
                className="fill-series-1"
                opacity={count > 0 ? Math.max(share, MINIMUM_SHADE) : 0}
              >
                <title>
                  {`${rowIndex >= bounds.length ? `over ${formatBound(bounds.at(-1), unit)}` : `up to ${formatBound(bounds[rowIndex], unit)}`}: ${formatCount(count)}`}
                </title>
              </rect>
            );
          }),
        )}
      </svg>

      {/* The scale the rows are drawn against. Without it the grid is a texture. */}
      <div
        aria-hidden="true"
        className="flex shrink-0 flex-col-reverse justify-between font-mono text-2xs text-ink-dim"
        style={{ height }}
      >
        <span>{formatBound(bounds[0], unit)}</span>
        <span>{formatBound(bounds.at(-1), unit)}</span>
      </div>
    </div>
  );
}

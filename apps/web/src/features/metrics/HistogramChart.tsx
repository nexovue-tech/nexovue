import { formatCount } from "@/shared/format/number";

import type { HistogramBar } from "./buckets";
import { axisLabels } from "./buckets";

/**
 * A histogram's buckets as bars, drawn in SVG against the token palette.
 *
 * Not uPlot: this is a categorical axis of at most a few dozen bars with no time in it,
 * and uPlot's whole value here — scales, cursors, streaming `setData` — is machinery
 * this chart has no use for. Plain SVG also renders under jsdom, so the drawing itself
 * is covered by the same tests as the rest of the screen rather than being the one part
 * nothing checks.
 *
 * Bars are evenly spaced. `buckets.ts` carries why: bucket bounds are exponential, so a
 * linear value axis would crush every interesting bucket against the left edge.
 */

const BAR_GAP = 2;
const HEIGHT = 160;
/** A bucket that holds something must be visible, even at 0.3% of the tallest. */
const MINIMUM_VISIBLE = 2;

interface HistogramChartProps {
  bars: readonly HistogramBar[];
  /** Read in place of the drawing, which says nothing on its own. */
  label: string;
}

export function HistogramChart({ bars, label }: HistogramChartProps) {
  if (bars.length === 0) return null;

  const width = bars.length * 10;
  const barWidth = Math.max(width / bars.length - BAR_GAP, 1);
  const labels = axisLabels(bars);

  return (
    <div>
      <svg
        role="img"
        aria-label={label}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="block h-40 w-full"
      >
        <title>{label}</title>
        {bars.map((bar, index) => {
          const drawn = bar.count > 0 ? Math.max(bar.height * HEIGHT, MINIMUM_VISIBLE) : 0;
          return (
            <rect
              key={bar.label}
              x={index * (barWidth + BAR_GAP)}
              y={HEIGHT - drawn}
              width={barWidth}
              height={drawn}
              // The open-ended bucket is drawn apart from the bounded ones: it covers
              // everything above the last boundary, so it is not comparable to a bar
              // that covers a known range, and it should not read as if it were.
              className={bar.unbounded ? "fill-series-3" : "fill-series-1"}
            >
              <title>{`${bar.label}: ${formatCount(bar.count)}`}</title>
            </rect>
          );
        })}
      </svg>

      {/* The boundaries, under the bars they belong to. Without these the chart is a
          row of heights with no scale, which is not a distribution — it is a shape. */}
      <div className="flex" aria-hidden="true">
        {labels.map((boundary, index) => (
          <span
            key={bars[index].label}
            className="min-w-0 flex-1 truncate pt-1 text-center font-mono text-2xs text-ink-dim"
          >
            {boundary ?? ""}
          </span>
        ))}
      </div>
    </div>
  );
}

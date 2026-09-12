/**
 * A trend inside a row, not a panel beside it.
 *
 * The design system's thesis is that the primary object is a sentence and everything
 * else is evidence for it: "checkout-api, 1.2k a minute, 4.1% failing, and rising". The
 * sparkline is the evidence for "and rising", so it is sized to a row and carries no
 * axis, no gridline, no legend and no tooltip — anything a reader has to interpret
 * belongs on the detail screen the row links to.
 *
 * **The scale belongs to the caller, never to the row.** `max` is required for that
 * reason. A per-row auto-scaled sparkline fills its own box whatever the numbers are, so
 * a flat service and a spiking one draw identically and the column stops being
 * comparable — which is the only reason to put a trend in a list at all.
 */

import { isReading, UNKNOWN } from "@/shared/format/number";

/**
 * Below this, a line is a shape invented out of two or three readings rather than a
 * trend. The em dash says so, on the same rule as every other absent reading.
 */
const MINIMUM_POINTS = 4;

const WIDTH = 104;
const HEIGHT = 20;
/** Half the stroke plus the endpoint dot, so neither is clipped at the edges. */
const PADDING = 2.5;

const STROKE_WIDTH = 1.5;
const ENDPOINT_RADIUS = 1.9;

interface SparklineProps {
  /** Oldest first. */
  points: readonly number[];
  /**
   * The top of the scale, shared across every sparkline on the screen. A caller that
   * passes each row's own maximum has rebuilt the auto-scaling this component exists to
   * refuse.
   */
  max: number;
  /**
   * `critical` when the series being summarised is the error series, so the stroke
   * agrees with the row's severity rather than contradicting it.
   */
  tone?: "series" | "critical";
  /** Read aloud in place of the drawing, which is decorative on its own. */
  label: string;
}

export function Sparkline({ points, max, tone = "series", label }: SparklineProps) {
  if (points.length < MINIMUM_POINTS || !isReading(max) || max <= 0) {
    return (
      <span className="block text-right font-mono text-2xs text-ink-dim" title={label}>
        {UNKNOWN}
      </span>
    );
  }

  const step = (WIDTH - PADDING * 2) / (points.length - 1);
  const scale = (value: number) => {
    // A reading we do not have sits on the floor rather than breaking the line: a gap
    // would read as "nothing happened", which is the opposite of "we did not measure".
    const measured = isReading(value) ? Math.min(value, max) : 0;
    return HEIGHT - PADDING - (measured / max) * (HEIGHT - PADDING * 2);
  };

  const coordinates = points.map((value, index) => ({
    x: PADDING + index * step,
    y: scale(value),
  }));
  const path = coordinates.map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = coordinates[coordinates.length - 1];

  const stroke = tone === "critical" ? "stroke-critical-solid" : "stroke-series-1";
  const fill = tone === "critical" ? "fill-critical-solid" : "fill-series-1";

  return (
    <svg
      role="img"
      aria-label={label}
      width={WIDTH}
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="block"
    >
      <polyline
        points={path}
        fill="none"
        strokeWidth={STROKE_WIDTH}
        strokeLinejoin="round"
        strokeLinecap="round"
        className={stroke}
      />
      {/* The newest reading, which is the one a scanning reader is looking for. */}
      <circle cx={last.x} cy={last.y} r={ENDPOINT_RADIUS} className={fill} />
    </svg>
  );
}

/**
 * Rendering a reading in the unit its exporter chose.
 *
 * Shared because two screens need it and neither may import the other: the metrics
 * catalogue draws a histogram's bounds, and a service's duration heatmap draws the same
 * bounds beside its rows. What they have in common is a number and an OTLP unit string,
 * which is a formatting concern rather than either screen's business.
 */

import { formatDecimalBytes } from "./bytes";
import { formatDuration } from "./duration";
import { formatCount, UNKNOWN } from "./number";

/** A metric's unit, in the OTLP spelling the SDK sends. */
const SECONDS = new Set(["s", "sec", "second", "seconds"]);
const MILLISECONDS = new Set(["ms", "millisecond", "milliseconds"]);
const BYTES = new Set(["By", "by", "byte", "bytes"]);

const MILLISECONDS_PER_SECOND = 1000;

/**
 * One bucket boundary, in whatever the metric is measured in.
 *
 * The unit is the exporter's own and is not guessed at. A duration renders as a
 * duration, a size as a size, and anything else renders as the number it is with its
 * unit beside it — inventing a scale for a unit we do not recognise would relabel
 * somebody's data.
 */
export function formatBound(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNKNOWN;

  if (SECONDS.has(unit)) return formatDuration(value * MILLISECONDS_PER_SECOND);
  if (MILLISECONDS.has(unit)) return formatDuration(value);
  if (BYTES.has(unit)) return formatDecimalBytes(value);

  const rendered = formatCount(value);
  return unit ? `${rendered} ${unit}` : rendered;
}

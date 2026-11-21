import type { Measure } from "@/shared/delta";
import { deltaOf } from "@/shared/delta";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";

import type { NumberReading } from "./api";

/**
 * A number panel's words as data (F20, T5.6a): its value in its measure's own unit, and
 * its comparison, said only when the previous window's data covers it (AC73).
 */

export const NOT_ENOUGH_HISTORY = "Not enough history to compare";

const FORMATS: Record<string, (value: number) => string> = {
  requests_per_minute: formatRatePerMinute,
  error_rate: formatPercent,
  p50_ms: formatDuration,
  p95_ms: formatDuration,
  p99_ms: formatDuration,
  service_count: formatCount,
};

/** How each measure is compared, and which way is the bad one (`shared/delta.ts`). */
const COMPARED: Record<string, { measure: Measure; worse: "up" | "down" }> = {
  requests_per_minute: { measure: "rate", worse: "down" },
  error_rate: { measure: "ratio", worse: "up" },
  p50_ms: { measure: "duration", worse: "up" },
  p95_ms: { measure: "duration", worse: "up" },
  p99_ms: { measure: "duration", worse: "up" },
  service_count: { measure: "count", worse: "down" },
};

export function formatMeasure(measure: string, value: number): string {
  return (FORMATS[measure] ?? formatCount)(value);
}

export interface Comparison {
  /** No comparison asked for, a delta worth showing or none, or too little history. */
  kind: "none" | "compared" | "not_enough_history";
  delta?: ReturnType<typeof deltaOf>;
  worse?: "up" | "down";
  previous?: string;
}

export function comparisonOf(number: NumberReading, compare: boolean): Comparison {
  if (!compare) return { kind: "none" };
  if (number.previous === null || number.previous === undefined) {
    return { kind: "not_enough_history" };
  }
  const how = COMPARED[number.measure] ?? { measure: "count" as const, worse: "up" as const };
  return {
    kind: "compared",
    delta: deltaOf(how.measure, number.value, number.previous),
    worse: how.worse,
    previous: formatMeasure(number.measure, number.previous),
  };
}

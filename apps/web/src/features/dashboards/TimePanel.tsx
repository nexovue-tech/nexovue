import type { SeriesToken } from "@/shared/components/TrendChart";
import { TrendChart } from "@/shared/components/TrendChart";
import { formatDuration } from "@/shared/format/duration";
import { formatRatePerMinute } from "@/shared/format/rate";

import type { Signal } from "./api";
import type { OkResult } from "./kept-readings";

/**
 * A rate, error or latency line, drawn as Overview draws the same reading: the error
 * share in percent and in the critical tone, the three percentiles on one scale.
 */

const MS_PER_SECOND = 1000;
const PERCENT = 100;

const LINE_NAMES: Record<string, string> = {
  requests_per_minute: "Requests/min",
  error_rate: "Failure share",
  p50_ms: "p50",
  p95_ms: "p95",
  p99_ms: "p99",
};

const TONE: Record<Signal, SeriesToken | "critical"> = { rate: 1, errors: "critical", latency: 2 };

const AXIS_FORMAT: Record<Signal, (value: number) => string> = {
  rate: (value) => formatRatePerMinute(value),
  errors: (value) => `${value.toFixed(1)}%`,
  latency: (value) => formatDuration(value),
};

export function TimePanel({
  reading,
  signal,
  description,
  zone,
}: {
  reading: OkResult;
  signal: Signal;
  description: string;
  zone: string;
}) {
  const [first] = reading.series;
  const x = (first?.points ?? []).map(([at]) => Date.parse(at) / MS_PER_SECOND);
  const lines = reading.series.map((series) =>
    series.points.map(([, value]) => (signal === "errors" ? value * PERCENT : value)),
  );
  return (
    <TrendChart
      label={reading.series.map((series) => LINE_NAMES[series.label] ?? series.label)}
      seriesToken={TONE[signal]}
      data={[x, ...lines]}
      formatValue={AXIS_FORMAT[signal]}
      description={description}
      zone={zone}
    />
  );
}

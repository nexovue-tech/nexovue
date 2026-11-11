import { useMemo, useState } from "react";

import { TrendChart } from "@/shared/components/TrendChart";

import type { StoredPanel } from "./api";
import { ChartLegend, readoutAt } from "./ChartLegend";
import { ChartTable } from "./ChartTable";
import type { OkResult } from "./kept-readings";
import type { Marks } from "./time-lines";
import { NO_MARKS, plotRows, timeChart } from "./time-lines";

/**
 * A time panel: the chart, the value at its cursor, or its numbers as a table (F20,
 * Step 4). Drawn as Overview draws the same reading: the failure share in percent and in
 * the critical tone, the three percentiles on one scale; a top-N chart's services each in
 * their own colour and dash; a node's reading off zero. The bucket still filling is left
 * off the line and read "so far" (FR66).
 *
 * Every chart on one dashboard shares `syncKey`, so the cursor shows one moment on all;
 * a drag hands its span to `onDrag` once, and moves no other chart itself (FR32, AC31).
 */
export function TimePanel({
  panel,
  reading,
  description,
  zone,
  syncKey,
  onDrag,
  marks = NO_MARKS,
  asTable = false,
}: {
  panel: StoredPanel;
  reading: OkResult;
  description: string;
  zone: string;
  syncKey?: string;
  onDrag?: (fromSeconds: number, toSeconds: number) => void;
  marks?: Marks;
  asTable?: boolean;
}) {
  const chart = useMemo(() => timeChart(panel, reading), [panel, reading]);
  const data = useMemo(() => plotRows(chart), [chart]);
  const labels = useMemo(() => chart.lines.map((line) => line.name), [chart]);
  const [cursor, setCursor] = useState<number | null>(null);

  if (asTable) {
    return <ChartTable chart={chart} marks={marks} zone={zone} caption={description} />;
  }
  return (
    <>
      <TrendChart
        label={labels}
        seriesToken={chart.tone}
        data={data}
        formatValue={chart.format}
        description={description}
        zone={zone}
        height={chart.height}
        {...(chart.yRange === "fit" ? { yRange: "fit" as const } : {})}
        distinctColours={chart.distinctColours}
        readout={setCursor}
        {...(syncKey === undefined ? {} : { syncKey })}
        {...(onDrag === undefined ? {} : { onSelectRange: onDrag })}
        spans={marks.spans}
        markers={marks.markers}
        dottedSeen
      />
      <ChartLegend readout={readoutAt(chart, cursor, marks, zone)} lines={chart.lines.length} />
    </>
  );
}

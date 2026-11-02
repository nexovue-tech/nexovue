import type { PlotRows } from "./TrendChart";

/**
 * A band between two of a chart's series: the space a reading may move in without
 * being unusual, for a finding (AC38). `upper` and `lower` index `PlotRows`, whose
 * first row is the x axis, as uPlot's series do.
 */
export interface ChartBand {
  upper: number;
  lower: number;
}

/** One unbroken stretch of the band, in data values, to fill as one shape. */
export interface BandStretch {
  xs: number[];
  upper: number[];
  lower: number[];
}

/**
 * The band's stretches where both edges are known. A minute missing either edge
 * breaks it, so the fill never bridges a gap the readings do not.
 */
export function bandStretches(data: PlotRows, band: ChartBand): BandStretch[] {
  const [xs] = data;
  const upper = data[band.upper];
  const lower = data[band.lower];
  if (upper === undefined || lower === undefined) return [];

  const stretches: BandStretch[] = [];
  let current: BandStretch | undefined;
  xs.forEach((x, index) => {
    const top = upper[index];
    const bottom = lower[index];
    if (top === null || top === undefined || bottom === null || bottom === undefined) {
      current = undefined;
      return;
    }
    if (current === undefined) {
      current = { xs: [], upper: [], lower: [] };
      stretches.push(current);
    }
    current.xs.push(x);
    current.upper.push(top);
    current.lower.push(bottom);
  });
  return stretches;
}

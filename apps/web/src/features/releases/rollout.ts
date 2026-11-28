/**
 * Each version's share of the service's traffic over time (FR26): the top four by name,
 * then "Other versions"; no version and the worker's fold read as labels. The chart and
 * the table below it say the same thing, so touch and screen readers get it too.
 */

import type { PlotRows } from "@/shared/components/TrendChart";
import { formatPercent } from "@/shared/format/number";
import { shortVersion } from "@/shared/format/version";

import type { Rollout } from "./api";

const MS_PER_SECOND = 1000;

export function seriesLabel(series: Rollout["series"][number]): string {
  switch (series.kind) {
    case "no_version":
      return "No version";
    case "combined":
      return "Combined versions";
    case "other_versions":
      return "Other versions";
    case "version":
      return shortVersion(series.version ?? "");
  }
}

/** Each minute's share per series, as a fraction; null for a minute with no traffic. */
export function shareRows(rollout: Rollout): PlotRows {
  const seconds = rollout.minutes.map((row) => Date.parse(row.minute) / MS_PER_SECOND);
  const series = rollout.series.map((_, index) =>
    rollout.minutes.map((row) => {
      const total = row.requests.reduce((sum, count) => sum + count, 0);
      return total > 0 ? (row.requests[index] ?? 0) / total : null;
    }),
  );
  return [seconds, ...series];
}

/** The latest minute with traffic: each series' share, for the description and the table. */
export function latestShares(rollout: Rollout): Array<{ label: string; share: string }> {
  const latest = [...rollout.minutes]
    .reverse()
    .find((row) => row.requests.some((count) => count > 0));
  if (latest === undefined) return [];
  const total = latest.requests.reduce((sum, count) => sum + count, 0);
  return rollout.series.map((series, index) => ({
    label: seriesLabel(series),
    share: formatPercent((latest.requests[index] ?? 0) / total),
  }));
}

export function rolloutDescription(rollout: Rollout): string {
  const shares = latestShares(rollout);
  if (shares.length === 0) return "No traffic counted on this service in the window.";
  return `Share of the service's requests by version. Latest minute: ${shares
    .map((each) => `${each.label} ${each.share}`)
    .join(", ")}.`;
}

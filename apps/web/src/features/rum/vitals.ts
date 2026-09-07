import { formatCount } from "@/shared/format/number";

import type { WebVitalGroup, WebVitalSummary } from "./api";

/**
 * What a Core Web Vital means once it is a number on a screen.
 *
 * The thresholds are `web-vitals@6.2.1`'s own, read out of the installed package
 * rather than transcribed from an article — it is the library the browser SDK uses to
 * produce each reading's `rating`, so the per-reading rating stored in `web_vitals`
 * and the p75's rating shown here are decided by one source. The rollup does not carry
 * a rating of its own: a rating is a property of a value, and the p75 is a new value.
 */

/** As the SDK spells them: `metric.name.toLowerCase()`. */
export const LCP = "lcp";
export const INP = "inp";
export const CLS = "cls";
export const FCP = "fcp";
export const TTFB = "ttfb";

export type Rating = "good" | "needs-improvement" | "poor" | "insufficient";

interface VitalDefinition {
  /** The acronym, which is what practitioners call it. */
  label: string;
  /** One line: what the number is actually measuring. */
  meaning: string;
  /** [good ceiling, poor floor] — at or below the first is good, above the second poor. */
  thresholds: readonly [number, number];
  /** CLS is a ratio; the other four are durations. */
  unit: "ms" | "ratio";
  /** The three Core Web Vitals rank above the two diagnostics. */
  core: boolean;
}

/**
 * The five, in the order the screen shows them: the three Core Web Vitals first,
 * because those are the ones a customer is measured on, then the two diagnostics that
 * explain a bad LCP.
 */
export const VITALS: ReadonlyArray<readonly [string, VitalDefinition]> = [
  [
    LCP,
    {
      label: "LCP",
      meaning: "How long until the largest thing on the page appeared.",
      thresholds: [2500, 4000],
      unit: "ms",
      core: true,
    },
  ],
  [
    INP,
    {
      label: "INP",
      meaning: "How long the page took to respond after someone interacted with it.",
      thresholds: [200, 500],
      unit: "ms",
      core: true,
    },
  ],
  [
    CLS,
    {
      label: "CLS",
      meaning: "How much the layout moved under the reader while it loaded.",
      thresholds: [0.1, 0.25],
      unit: "ratio",
      core: true,
    },
  ],
  [
    FCP,
    {
      label: "FCP",
      meaning: "How long until anything at all was painted.",
      thresholds: [1800, 3000],
      unit: "ms",
      core: false,
    },
  ],
  [
    TTFB,
    {
      label: "TTFB",
      meaning: "How long the server took to send the first byte.",
      thresholds: [800, 1800],
      unit: "ms",
      core: false,
    },
  ],
];

const BY_NAME = new Map(VITALS);

/**
 * Below this many readings the screen shows the count and not the percentile.
 *
 * Migration 000031 stores `reading_count` for exactly this. The reason is what a
 * quantile is: over four readings the p75 interpolates between the third and fourth,
 * so it is the second-slowest page load of four and moves entirely when one visitor
 * has a bad morning. Ten is not a statistical guarantee, it is the point past which
 * the number stops describing individuals.
 */
export const MIN_READINGS = 10;

export interface ReadableVital {
  name: string;
  label: string;
  meaning: string;
  core: boolean;
  rating: Rating;
  /** "2.4 s", "0.08", "180 ms" — already carrying its unit. */
  display: string;
  readings: number;
}

/** Where a value sits against its own thresholds, or that there is not enough of it. */
export function rate(name: string, value: number, readings: number): Rating {
  if (readings < MIN_READINGS) return "insufficient";
  const definition = BY_NAME.get(name);
  if (definition === undefined) return "insufficient";

  const [goodCeiling, poorFloor] = definition.thresholds;
  if (value <= goodCeiling) return "good";
  if (value <= poorFloor) return "needs-improvement";
  return "poor";
}

/**
 * A vital's value as a person reads it.
 *
 * Seconds past a second, because "2.4 s" is a number a human holds and "2412 ms" is
 * one they convert. CLS carries no unit at all: it is a ratio, and writing one after
 * it would invent a dimension the metric does not have.
 */
export function formatValue(name: string, value: number): string {
  const definition = BY_NAME.get(name);
  if (definition?.unit === "ratio") return value.toFixed(2);
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
  return `${Math.round(value)} ms`;
}

/**
 * The five vitals in their fixed order, whether or not each was reported.
 *
 * Fixed because a card that moves between refreshes cannot be found twice, and because
 * a missing vital is information: CLS is never reported outside Chromium, and an INP
 * that is absent means nobody interacted with the page.
 */
export function readableVitals(summaries: readonly WebVitalSummary[]): ReadableVital[] {
  const reported = new Map(summaries.map((summary) => [summary.name, summary]));

  return VITALS.map(([name, definition]) => {
    const summary = reported.get(name);
    return {
      name,
      label: definition.label,
      meaning: definition.meaning,
      core: definition.core,
      rating: summary === undefined ? "insufficient" : rate(name, summary.p75, summary.readings),
      display: summary === undefined ? "—" : formatValue(name, summary.p75),
      readings: summary?.readings ?? 0,
    };
  });
}

/* --------------------------------------------------------- the breakdowns */

/** A measurement the sample size will not support. Never a zero. */
export const UNMEASURED = "—";

/** What an empty `key` is called: the browser sent no path or no device class. */
export const UNLABELLED_GROUP = "not reported";

/** Marks a row whose key is a route template rather than one URL. */
export const ROUTE_TEMPLATE_TAG = "route";

/** How many readings a cell rests on, in the words the cards already use. */
export function describeReadings(readings: number): string {
  if (readings === 0) return "not reported on this row";
  if (readings < MIN_READINGS) {
    return `${formatCount(readings)} readings — too few for a percentile`;
  }
  return `${formatCount(readings)} readings`;
}

export interface BreakdownCell extends ReadableVital {
  /** Why the cell reads as it does. Always present, because a dash must explain itself. */
  detail: string;
}

export interface ReadableGroup {
  key: string;
  /** The page or device class as a person reads it. */
  label: string;
  /** True when `label` is a route template, which is a page rather than one URL. */
  isRouteTemplate: boolean;
  cells: BreakdownCell[];
}

/**
 * One breakdown row: the same five vitals, judged by the same thresholds.
 *
 * A split makes every count smaller — `MIN_READINGS` mattered at the site level and
 * decides most cells at the page level — so a cell under the threshold shows a dash and
 * its count rather than the percentile. On a card a dimmed number sits beside the words
 * "Not enough data"; in a table there is no room for that, and a fast-looking number in
 * a column of real ones invites exactly the comparison it cannot support.
 */
export function readableGroup(group: WebVitalGroup): ReadableGroup {
  return {
    key: group.key,
    label: group.key === "" ? UNLABELLED_GROUP : group.key,
    isRouteTemplate: group.is_route_template,
    cells: readableVitals(group.vitals).map((vital) => ({
      ...vital,
      display: vital.rating === "insufficient" ? UNMEASURED : vital.display,
      detail: describeReadings(vital.readings),
    })),
  };
}

/**
 * The sentence under the heading: what these numbers say, in one line.
 *
 * It names the worst Core Web Vital rather than summarising all five, because that is
 * the one a reader would act on, and says plainly when there is not enough data instead
 * of describing a percentile drawn from three page loads.
 */
export function describeVitals(vitals: readonly ReadableVital[], windowLabel: string): string {
  const measured = vitals.filter((vital) => vital.rating !== "insufficient");
  if (measured.length === 0) {
    const readings = vitals.reduce((total, vital) => total + vital.readings, 0);
    return readings === 0
      ? `No browser reported a Core Web Vital in the last ${windowLabel}.`
      : `Only ${readings} readings arrived in the last ${windowLabel} — too few to describe a 75th percentile.`;
  }

  const core = measured.filter((vital) => vital.core);
  const worst =
    (core.length > 0 ? core : measured).find((vital) => vital.rating === "poor") ??
    (core.length > 0 ? core : measured).find((vital) => vital.rating === "needs-improvement");

  if (worst === undefined) {
    return `Every Core Web Vital measured in the last ${windowLabel} is inside Google's "good" threshold.`;
  }
  return `${worst.label} is ${worst.display} at the 75th percentile over the last ${windowLabel}, which Google rates ${worst.rating === "poor" ? "poor" : "as needing improvement"}.`;
}

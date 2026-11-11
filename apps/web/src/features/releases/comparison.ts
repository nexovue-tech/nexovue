/**
 * What a comparison says, in words (F28 design, *Opening sentence*; FR24, FR25).
 *
 * Pure, so every method and reason is held to its exact sentence (AC66). The sentence
 * names its method (BR5) and reads from the leading block, the most recent settled one
 * (FR47). It compares numbers and never says why they moved (BR1): no word here may imply
 * a cause.
 */

import { formatCount, formatPercent } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { shortVersion } from "@/shared/format/version";

import type { Block, Comparison, Reading, Side, WindowHours } from "./api";
import { WINDOW_HOURS } from "./api";

const MINUTES_PER_HOUR = 60;
const MS_PER_MINUTE = 60_000;
/** A reported version not yet seen reads "waiting" this long (spec, *States*; a guess). */
const WAITING_FOR_TRAFFIC_MINUTES = 30;
/** Each version's first minutes are left out of p95 (FR48), as the API does. */
export const WARM_UP_MINUTES = 5;

const READING_WORDS: Record<Reading, string> = {
  higher: "higher",
  lower: "lower",
  about_same: "about the same",
  not_clearly_different: "not clearly different",
};

export const BELOW_THE_NUMBERS = "This compares numbers. It does not say why they moved.";
export const NO_SAMPLING = "Counts assume you do not sample before sending.";
export const WARM_UP_RULE = `p95 leaves out each version's first ${WARM_UP_MINUTES} minutes, while new instances start cold.`;

export function readingWord(reading: Reading | null): string {
  return reading === null ? "" : READING_WORDS[reading];
}

/** FR24: higher is critical text with its word; every other reading dim ink. Never green. */
export function readingTone(reading: Reading | null): string {
  return reading === "higher" ? "text-critical-text" : "text-ink-dim";
}

function failurePercent(side: Side): string {
  return formatPercent(side.requests > 0 ? side.errors / side.requests : null);
}

function milliseconds(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)} ms`;
}

/** "42 minutes", or "1 hour" and "6 hours" on the hour. */
export function stretchWords(minutes: number): string {
  if (minutes >= MINUTES_PER_HOUR && minutes % MINUTES_PER_HOUR === 0) {
    const hours = minutes / MINUTES_PER_HOUR;
    return `${hours} ${plural(hours, "hour")}`;
  }
  return `${minutes} ${plural(minutes, "minute")}`;
}

function failureClause(block: Block, byVersion: boolean): string {
  const { reading } = block.failure_share;
  if (reading === null || reading === "not_clearly_different") {
    return `failure share ${readingWord(reading) || "not clearly different"}.`;
  }
  const mine = failurePercent(block.this_version);
  const theirs = failurePercent(block.previous_version);
  const sides = byVersion
    ? `${mine} on ${shortVersion(block.this_version.version)} against ${theirs} on ${shortVersion(block.previous_version.version)}`
    : `${mine} against ${theirs}`;
  return `failure share ${sides}, ${readingWord(reading)}.`;
}

function p95Clause(block: Block): string {
  const { reading, note } = block.p95;
  if (note === "warm_up") {
    return `p95 is left out for each version's first ${WARM_UP_MINUTES} minutes.`;
  }
  if (reading === null) {
    const mine = block.this_version.requests;
    const theirs = block.previous_version.requests;
    const counts =
      mine === theirs
        ? `${formatCount(mine)} ${plural(mine, "request")} each`
        : `${formatCount(mine)} on ${shortVersion(block.this_version.version)}, ${formatCount(theirs)} on ${shortVersion(block.previous_version.version)}`;
    return `Too little traffic to read p95 (${counts}).`;
  }
  return `p95 ${milliseconds(block.this_version.p95_ms)} against ${milliseconds(block.previous_version.p95_ms)}, ${readingWord(reading)}.`;
}

function blockSentence(block: Block): string {
  const over = stretchWords(block.minutes_compared);
  if (block.method === "side_by_side") {
    return `Side by side over ${over}: ${failureClause(block, true)} ${p95Clause(block)}`;
  }
  if (block.method === "version_before_after") {
    return `Before and after, by version, over ${over}: ${failureClause(block, true)} ${p95Clause(block)} Different times of day can move these numbers.`;
  }
  return `Before and after, by time, over ${over}: ${failureClause(block, false)} No version in your traffic, so a rolling deploy mixes old and new here.`;
}

function reasonSentence(comparison: Comparison, startedAt: string, now: number): string {
  switch (comparison.reason) {
    case "too_recent":
      return "Under 5 settled minutes so far. Refreshes on its own.";
    case "no_previous_version":
      return "This is the first version we know of on this service, so there is nothing to compare it with.";
    case "already_running":
      return "Running when we started counting. Comparisons start from the next rollout.";
    case "too_little_traffic": {
      const traffic = comparison.traffic;
      if (traffic === null) return "Too little traffic to compare.";
      return `Too little traffic to compare: ${formatCount(traffic.this_version.requests)} ${plural(traffic.this_version.requests, "request")} on ${shortVersion(traffic.this_version.version)}, ${formatCount(traffic.previous_version.requests)} on ${shortVersion(traffic.previous_version.version)}.`;
    }
    case "numbers_expired":
      return `Numbers for this release have expired (kept ${comparison.numbers_kept_days} days on this project).`;
    case "no_version_no_deploy":
      return "No version in your traffic, and no deploy reported, so we cannot compare.";
    case "new_version_not_seen": {
      const waited = (now - Date.parse(startedAt)) / MS_PER_MINUTE;
      if (waited < WAITING_FOR_TRAFFIC_MINUTES) {
        return `Waiting for traffic on ${shortVersion(comparison.version)}.`;
      }
      const others = comparison.other_versions.map((version) => shortVersion(version)).join(", ");
      return `Reported, never seen in traffic. This service sends other versions (${others}). The rollout may have failed, or the version text may differ.`;
    }
    case "no_request_traffic":
      return "We count requests from server traces and queue consumers. This service sends none, so we can mark its deploys but cannot compare them. This is expected for a service that only runs in the browser.";
    default:
      return "";
  }
}

/** The page's first sentence: the leading block's numbers, or the reason there are none. */
export function openingSentence(
  comparison: Comparison,
  { startedAt, now }: { startedAt: string; now: number },
): string {
  if (comparison.reason !== null) return reasonSentence(comparison, startedAt, now);
  const lead = comparison.blocks.find((block) => block.kind === comparison.lead);
  return lead === undefined ? "" : blockSentence(lead);
}

export interface MeasureLine {
  label: string;
  reading: Reading | null;
  /** The plain word, or empty where the measure takes none (traffic share). */
  word: string;
  /** One entry per side: this version first. */
  sides: [string, string];
}

/**
 * Each measure of a block, as the table's rows and the phone's stacked lines: failure
 * share and p95 with their words, then share of traffic side by side, or requests a
 * minute before and after (FR25). Traffic share has no colour and no word.
 */
export function measureLines(block: Block): MeasureLine[] {
  const mine = block.this_version;
  const theirs = block.previous_version;
  const name = (side: Side) => shortVersion(side.version) || "No version";
  const failure: MeasureLine = {
    label: "Failure share",
    reading: block.failure_share.reading,
    word:
      block.failure_share.reading === null
        ? "too little traffic"
        : readingWord(block.failure_share.reading),
    sides: [
      `${name(mine)} ${failurePercent(mine)} (${formatCount(mine.errors)} of ${formatCount(mine.requests)})`,
      `${name(theirs)} ${failurePercent(theirs)} (${formatCount(theirs.errors)} of ${formatCount(theirs.requests)})`,
    ],
  };
  const p95: MeasureLine = {
    label: "p95",
    reading: block.p95.reading,
    word:
      block.p95.note === "warm_up"
        ? "left out for warm-up"
        : block.p95.reading === null
          ? "too little traffic"
          : readingWord(block.p95.reading),
    sides: [
      `${name(mine)} ${milliseconds(mine.p95_ms)}`,
      `${name(theirs)} ${milliseconds(theirs.p95_ms)}`,
    ],
  };
  if (block.method === "side_by_side") {
    const both = mine.requests + theirs.requests;
    return [
      failure,
      p95,
      {
        label: "Share of traffic",
        reading: null,
        word: "",
        sides: [
          `${name(mine)} ${formatPercent(both > 0 ? mine.requests / both : null)}`,
          `${name(theirs)} ${formatPercent(both > 0 ? theirs.requests / both : null)}`,
        ],
      },
    ];
  }
  const perMinute = (side: Side) =>
    block.minutes_compared > 0 ? (side.requests / block.minutes_compared).toFixed(1) : "—";
  return [
    failure,
    p95,
    {
      label: "Requests a minute",
      reading: null,
      word: "",
      sides: [`${name(mine)} ${perMinute(mine)}`, `${name(theirs)} ${perMinute(theirs)}`],
    },
  ];
}

/* ------------------------------------------ opened from a finding (FR22, AC64) */

const MS_PER_HOUR = 3_600_000;

export interface FindingWindow {
  hours: WindowHours;
  /** Whether the window reaches the finding's start; the largest when none does. */
  reaches: boolean;
}

/**
 * The smallest offered window from the period's start that reaches a finding's start.
 * A release 5 hours before compares over 6, so it does not read "about the same" over a
 * healthy first hour while the service fails later.
 */
export function windowForFinding(periodStart: string, findingStart: string): FindingWindow {
  const gap = Date.parse(findingStart) - Date.parse(periodStart);
  const reaching = WINDOW_HOURS.find((hours) => hours * MS_PER_HOUR >= gap);
  return reaching === undefined
    ? { hours: WINDOW_HOURS[WINDOW_HOURS.length - 1] as WindowHours, reaches: false }
    : { hours: reaching, reaches: true };
}

export function findingWindowSentence(choice: FindingWindow, findingStartWords: string): string {
  return choice.reaches
    ? `Compared over ${choice.hours} ${plural(choice.hours, "hour")}, to include the finding that started at ${findingStartWords}.`
    : "This comparison ends before that finding started, so it says nothing about it.";
}

export function windowWords(hours: WindowHours): string {
  return `Compared over ${hours} ${plural(hours, "hour")} from the start.`;
}

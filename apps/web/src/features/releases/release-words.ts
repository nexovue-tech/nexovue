/**
 * How a release reads in a row and in its facts (F28 spec, *States*; design, `/releases`).
 * Order only, never cause (BR1); reported and seen kept apart, and "seen" never called
 * a deploy (BR3).
 */

import { formatCount, formatPercent } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { formatGap } from "@/shared/format/time";
import { shortVersion } from "@/shared/format/version";
import { formatZonedTime } from "@/shared/format/zoned";

import type { Activity, Basis, NamingDiffers, StoredFlag } from "./api";

const MS_PER_MINUTE = 60_000;
/** A reported version not seen yet reads "waiting" this long (spec, *States*; a guess). */
const WAITING_MINUTES = 30;

const BASIS_WORDS: Record<Basis, string> = {
  reported: "Reported by your CI",
  seen: "Seen in traffic",
  both: "Reported by your CI, seen in traffic",
};

export function basisWords(basis: Basis): string {
  return BASIS_WORDS[basis];
}

export function activityWords(
  activity: Activity,
  {
    startedAt,
    lastSeenAt,
    replacedBy,
    zone,
    now,
  }: {
    startedAt: string;
    lastSeenAt: string | null;
    replacedBy?: string | null;
    zone: string | undefined;
    now: number;
  },
): string {
  switch (activity) {
    case "not_seen":
      return (now - Date.parse(startedAt)) / MS_PER_MINUTE < WAITING_MINUTES
        ? "Waiting for traffic"
        : "Reported, never seen in traffic. The rollout may have failed, or the service name or version text may differ";
    case "serving":
      return "Serving";
    case "replaced":
      return replacedBy ? `Replaced by ${shortVersion(replacedBy)}` : "Replaced";
    case "idle":
      return lastSeenAt === null
        ? "No traffic lately"
        : `No traffic since ${formatZonedTime(lastSeenAt, { zone, now })}`;
  }
}

/** What a sender said beside the activity: both are facts (spec, *States*). */
export function stateWords(lastState: string | null): string | null {
  if (lastState === "failed") return "CI reported it failed";
  if (lastState === "rolled_back") return "CI reported it rolled back";
  return null;
}

/** "12 releases in this window on 4 services." */
export function windowCount(total: number, services: number): string {
  return `${total} ${plural(total, "release")} in this window on ${services} ${plural(services, "service")}.`;
}

/** "The latest was v2.1 of checkout, 3 days ago." */
export function latestWords(
  version: string,
  service: string,
  startedAt: string,
  now: number,
): string {
  const gap = formatGap(startedAt, new Date(now).toISOString());
  const when = gap === undefined ? "" : `, ${gap} ago`;
  return `The latest was ${shortVersion(version)} of ${service}${when}.`;
}

/** FR46, in place of the "never seen" words on that service's rows (AC78). */
export function namingDiffersWords(differs: NamingDiffers): string {
  return `Your CI and your traffic name versions differently: CI sent ${shortVersion(differs.reported_version)}, traffic sends ${shortVersion(differs.seen_version)}`;
}

function failureShare(errors: number, requests: number): string {
  const share = formatPercent(requests > 0 ? errors / requests : null);
  return `${share} (${formatCount(errors)} of ${formatCount(requests)})`;
}

function milliseconds(value: number | null): string {
  return value === null ? "—" : `${formatCount(value)} ms`;
}

/**
 * FR55, AC81: "Failure share higher: 3.0% (180 of 6,012) against 0.2% (12 of 5,844)", or
 * "p95 higher: 412 ms against 290 ms", from the reading a job stored. Numbers, not a
 * verdict: the new version against the one it replaced, in the order the page reads.
 */
export function flagWords(flag: StoredFlag): string {
  if (flag.measure === "failure_share") {
    return `Failure share higher: ${failureShare(flag.this_errors, flag.this_requests)} against ${failureShare(flag.previous_errors, flag.previous_requests)}`;
  }
  return `p95 higher: ${milliseconds(flag.this_p95_ms)} against ${milliseconds(flag.previous_p95_ms)}`;
}

/** The list's over-cap state (design, `/releases`), from the stored readings (FR17). */
export function combinedWords(services: string[]): string {
  return `Versions were combined on ${services.join(", ")}: too many in one minute.`;
}

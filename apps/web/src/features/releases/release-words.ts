/**
 * How a release reads in a row and in its facts (F28 spec, *States*; design, `/releases`).
 * Order only, never cause (BR1); reported and seen kept apart, and "seen" never called
 * a deploy (BR3).
 */

import { plural } from "@/shared/format/plural";
import { formatGap } from "@/shared/format/time";
import { shortVersion } from "@/shared/format/version";
import { formatZonedTime } from "@/shared/format/zoned";

import type { Activity, Basis } from "./api";

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

import { formatZonedTime, zoneLabel } from "@/shared/format/zoned";
import type { WindowChoice } from "@/shared/selection";
import { timeRangeById } from "@/shared/selection";

import type { DashboardSearch } from "./dashboard-search";
import { addressOf } from "./dashboard-search";

/**
 * "Copy link" offers both, and says which it copied (F20, FR29, AC137).
 *
 * **Exact times** open the same minutes for whoever follows them, however late: what to
 * send a colleague about a spike. **A link that stays live** opens the last hour (or
 * whichever range) as of whenever it is opened: what to pin on a wall. Exact times come
 * first when the window was dragged or fixed, since that is what the sender is looking at.
 *
 * Both name the project, and the picked service by its name, so a link never reads
 * against the follower's own project (AC27). Full screen is never in a link.
 */

export interface LinkTo {
  origin: string;
  pathname: string;
  stringify: (search: Record<string, unknown>) => string;
  projectId: string | undefined;
  service: string | undefined;
}

function hrefFor(base: LinkTo, search: DashboardSearch): string {
  return `${base.origin}${base.pathname}${base.stringify(search as Record<string, unknown>)}`;
}

export function exactTimesLink(base: LinkTo, times: { from: string; to: string }): string {
  return hrefFor(
    base,
    addressOf({
      project: base.projectId,
      window: { kind: "absolute", ...times },
      service: base.service,
    }),
  );
}

export function liveLink(base: LinkTo, rangeId: string): string {
  return hrefFor(
    base,
    addressOf({
      project: base.projectId,
      window: { kind: "relative", rangeId },
      service: base.service,
    }),
  );
}

/** Exact times first when the window was dragged or fixed (FR29). */
export function exactTimesFirst(choice: WindowChoice): boolean {
  return choice.kind === "absolute";
}

/** "02:00 to 03:00", the zone named once. */
export function describeTimes(
  times: { from: string; to: string },
  zone: string,
  now?: number,
): string {
  const from = formatZonedTime(times.from, { zone, now });
  const to = formatZonedTime(times.to, { zone, now });
  // One zone name for the pair, as "02:00 to 03:00 IST" reads.
  const label = zoneLabel(zone, new Date(times.to));
  const start = from.endsWith(` ${label}`) ? from.slice(0, -label.length - 1) : from;
  return `${start} to ${to}`;
}

/** The live choice's words: "last hour". */
export function liveWords(rangeId: string): string {
  return `last ${timeRangeById(rangeId).label}`;
}

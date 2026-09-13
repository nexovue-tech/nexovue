/**
 * Timestamps in two registers: the wall clock for lining spans up against each other,
 * and "4 min ago" for answering "is this recent" without arithmetic.
 *
 * An unparseable timestamp is data we cannot trust, so it is shown as absent.
 */

import { UNKNOWN } from "./number";
import { plural } from "./plural";

const CLOCK_TIME = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hour12: false,
});

const FULL_TIMESTAMP = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

const CALENDAR_DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });

const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3600;
const SECONDS_PER_DAY = 86_400;

function parse(iso: string): Date | undefined {
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? undefined : value;
}

export function formatClockTime(iso: string): string {
  const value = parse(iso);
  return value ? CLOCK_TIME.format(value) : UNKNOWN;
}

export function formatTimestamp(iso: string): string {
  const value = parse(iso);
  return value ? FULL_TIMESTAMP.format(value) : UNKNOWN;
}

export function formatDate(iso: string): string {
  const value = parse(iso);
  return value ? CALENDAR_DATE.format(value) : UNKNOWN;
}

/** Under this, a gap is not worth a number — "0 seconds after" reads as an error. */
const UNDER_A_SECOND = "under a second";

/**
 * How much time separates two instants: "4 minutes", "3 hours", "2 days".
 *
 * A quantity with no direction and no "ago", which is what two moments both in the
 * past need — `formatRelativeTime` words a gap against *now* and would say "in 4
 * min" for one measured between two other times.
 *
 * Undefined when either timestamp is unreadable, or when the later one is not
 * later: a gap nobody measured is absent, never zero.
 */
export function formatGap(earlierIso: string, laterIso: string): string | undefined {
  const earlier = parse(earlierIso);
  const later = parse(laterIso);
  if (!earlier || !later) return undefined;

  const elapsedMs = later.getTime() - earlier.getTime();
  if (elapsedMs < 0) return undefined;
  if (elapsedMs < MS_PER_SECOND) return UNDER_A_SECOND;

  const seconds = Math.round(elapsedMs / MS_PER_SECOND);
  if (seconds < SECONDS_PER_MINUTE) return `${seconds} ${plural(seconds, "second")}`;

  const minutes = Math.round(seconds / SECONDS_PER_MINUTE);
  if (seconds < SECONDS_PER_HOUR) return `${minutes} ${plural(minutes, "minute")}`;

  const hours = Math.round(seconds / SECONDS_PER_HOUR);
  if (seconds < SECONDS_PER_DAY) return `${hours} ${plural(hours, "hour")}`;

  const days = Math.round(seconds / SECONDS_PER_DAY);
  return `${days} ${plural(days, "day")}`;
}

export function formatRelativeTime(iso: string, now = Date.now()): string {
  const value = parse(iso);
  if (!value) return UNKNOWN;

  const elapsedSeconds = (value.getTime() - now) / MS_PER_SECOND;
  const magnitude = Math.abs(elapsedSeconds);

  if (magnitude < SECONDS_PER_MINUTE) return RELATIVE.format(Math.round(elapsedSeconds), "second");
  if (magnitude < SECONDS_PER_HOUR) {
    return RELATIVE.format(Math.round(elapsedSeconds / SECONDS_PER_MINUTE), "minute");
  }
  if (magnitude < SECONDS_PER_DAY) {
    return RELATIVE.format(Math.round(elapsedSeconds / SECONDS_PER_HOUR), "hour");
  }
  return RELATIVE.format(Math.round(elapsedSeconds / SECONDS_PER_DAY), "day");
}

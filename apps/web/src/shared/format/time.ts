/**
 * Timestamps in two registers: the wall clock for lining spans up against each other,
 * and "4 min ago" for answering "is this recent" without arithmetic.
 *
 * An unparseable timestamp is data we cannot trust, so it is shown as absent.
 */

import { UNKNOWN } from "./number";

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

/**
 * Which days the screen asks for.
 *
 * Days, not the shared time picker: usage is metered per calendar day, and a fifteen
 * minute window has no row to return. The days are UTC because the meter counts in UTC
 * — asking in the reader's zone would put half of their "today" in a day the API has
 * already called finished.
 */

export interface DayRange {
  days: number;
  /** The button's own word, reused in the sentence under the title. */
  label: string;
  /** `YYYY-MM-DD`, inclusive at both ends. */
  from: string;
  to: string;
}

export const RANGE_OPTIONS: ReadonlyArray<{ days: number; label: string }> = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

export const DEFAULT_RANGE_DAYS = 30;

const MS_PER_DAY = 86_400_000;

export function labelForDays(days: number): string {
  return RANGE_OPTIONS.find((option) => option.days === days)?.label ?? `${days} days`;
}

/** The UTC calendar day a moment falls in, as the API writes days. */
export function utcDay(at: number): string {
  return new Date(at).toISOString().slice(0, 10);
}

/**
 * The last `days` days, today included. Ends are inclusive on both sides, so seven days
 * is today and the six before it — asking for `now - 7 days` would be eight.
 */
export function usageRange(days: number, now = Date.now()): DayRange {
  return {
    days,
    label: labelForDays(days),
    from: utcDay(now - (days - 1) * MS_PER_DAY),
    to: utcDay(now),
  };
}

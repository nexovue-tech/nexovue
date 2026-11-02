/**
 * Instants on a named clock: "14:02 London time", "14:02 EDT".
 *
 * `time.ts` formats in whatever zone the browser is in, which is right for lining spans
 * up and wrong for a sentence a team shares: the same finding must read the same in
 * Bengaluru and in Dublin. So these take a zone, and always name it.
 *
 * **Which name (the rule).** A zone is labelled by its abbreviation only when both of its
 * abbreviations — January's and July's, so its winter and summer sides — are letters
 * and name one zone alone. Otherwise it is labelled by its city: "Kolkata time".
 * - "IST" is India, Ireland and Israel; "CST" is Chicago, China and Cuba; "BST" is
 *   London and Bangladesh. A reader cannot tell which, so those zones get their city.
 * - Where the runtime has no abbreviation it prints an offset ("GMT+5:30"), which is
 *   correct but says nothing about whose clock it is, so that zone gets its city too.
 * - Decided per zone, not per instant: London is "London time" in October and in
 *   July, rather than "GMT" on one side of the clock change and something else on the
 *   other.
 * - A fixed-offset zone (`Etc/GMT+5`) has no city, and its offset is its name.
 *
 * Abbreviations are read in a fixed locale, so a label does not change with the
 * reader's machine. An unknown zone falls back to UTC, labelled as UTC, rather than
 * printing a time on a clock it does not name.
 */

import { UNKNOWN } from "./number";

/** Read in en-US: it has the North American abbreviations and prints offsets for the rest. */
const ABBREVIATION_LOCALE = "en-US";
/** en-GB for the words around the time: FR25 writes "3 Nov" and a 24-hour "14:02". */
const WORDS_LOCALE = "en-GB";
const FALLBACK_ZONE = "UTC";

/** Abbreviations that more than one zone in common use answers to. */
const AMBIGUOUS = new Set(["AST", "ADT", "BST", "CST", "CDT", "GST", "IST"]);
const LETTERS_ONLY = /^[A-Z]{2,5}$/;

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(key: string, make: () => Intl.DateTimeFormat): Intl.DateTimeFormat {
  let found = formatters.get(key);
  if (found === undefined) {
    found = make();
    formatters.set(key, found);
  }
  return found;
}

/** The zone the browser is in: the default until a project names its own. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function knownZone(zone: string): string {
  try {
    new Intl.DateTimeFormat(ABBREVIATION_LOCALE, { timeZone: zone });
    return zone;
  } catch {
    return FALLBACK_ZONE;
  }
}

function abbreviationAt(zone: string, at: Date): string {
  const parts = formatter(`abbr|${zone}`, () => {
    return new Intl.DateTimeFormat(ABBREVIATION_LOCALE, { timeZone: zone, timeZoneName: "short" });
  }).formatToParts(at);
  return parts.find((part) => part.type === "timeZoneName")?.value ?? zone;
}

function cityOf(zone: string): string {
  const city = zone.slice(zone.lastIndexOf("/") + 1).replaceAll("_", " ");
  return `${city} time`;
}

/** How the clock `zone` is named at `at`, by the rule above. */
export function zoneLabel(zone: string, at: Date): string {
  const known = knownZone(zone);
  const here = abbreviationAt(known, at);
  if (known.startsWith("Etc/")) return here;

  const year = at.getUTCFullYear();
  const sides = [new Date(Date.UTC(year, 0, 15)), new Date(Date.UTC(year, 6, 15))];
  const plain = sides
    .map((side) => abbreviationAt(known, side))
    .every((name) => LETTERS_ONLY.test(name) && !AMBIGUOUS.has(name));
  return plain ? here : cityOf(known);
}

interface ZonedParts {
  day: string;
  month: string;
  year: string;
  time: string;
}

function partsIn(zone: string, at: Date): ZonedParts {
  const parts = formatter(`words|${zone}`, () => {
    return new Intl.DateTimeFormat(WORDS_LOCALE, {
      timeZone: zone,
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
  }).formatToParts(at);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((found) => found.type === type)?.value ?? "";
  return {
    day: part("day"),
    month: part("month"),
    year: part("year"),
    time: `${part("hour")}:${part("minute")}`,
  };
}

export interface ZonedOptions {
  /** An IANA zone. The browser's when absent, until the project's own is read. */
  zone?: string;
  /** The reader's now, which decides whether the day needs saying. */
  now?: number;
}

/**
 * "14:02 London time" for an instant today in that zone, and "30 Oct, 14:02 London time"
 * for one on another day: "since 14:02" must not mean yesterday's 14:02. The year is
 * added only when it is not this one.
 */
export function formatZonedTime(
  iso: string,
  { zone, now = Date.now() }: ZonedOptions = {},
): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return UNKNOWN;

  const known = knownZone(zone ?? browserTimeZone());
  const then = partsIn(known, at);
  const today = partsIn(known, new Date(now));
  const label = zoneLabel(known, at);

  if (then.day === today.day && then.month === today.month && then.year === today.year) {
    return `${then.time} ${label}`;
  }
  const year = then.year === today.year ? "" : ` ${then.year}`;
  return `${then.day} ${then.month}${year}, ${then.time} ${label}`;
}

const CALENDAR_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * "3 Nov" for `2026-11-03`: a day already on the project's calendar, so no zone moves
 * it. Anything that is not a real date is shown as absent.
 */
export function formatCalendarDay(day: string): string {
  const match = CALENDAR_DAY.exec(day);
  if (match === null) return UNKNOWN;
  const [, year, month, date] = match.map(Number);
  const at = new Date(Date.UTC(year ?? 0, (month ?? 0) - 1, date ?? 0));
  if (at.getUTCMonth() !== (month ?? 0) - 1 || at.getUTCDate() !== date) return UNKNOWN;

  const parts = partsIn(FALLBACK_ZONE, at);
  return `${parts.day} ${parts.month}`;
}

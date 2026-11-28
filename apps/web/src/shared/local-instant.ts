/**
 * Moving between an instant and what a `datetime-local` input speaks.
 *
 * The input has no timezone: it shows and returns wall-clock text in whatever zone the
 * reader's machine is in. Everything else in this product is an ISO instant in UTC,
 * because a window is compared against telemetry from machines in other zones.
 *
 * **The conversion is where an off-by-one-timezone bug lives**, and it is invisible in
 * the only place most people test it — a machine set to UTC. Hence a pure module with
 * its own tests rather than two `slice` calls inside a component.
 */

/**
 * `2026-09-08T14:00:00Z` in Berlin becomes `2026-09-08T16:00`, which is what Berlin sees.
 * With a zone, what that zone's clock shows: a dashboard's picker is on the project's
 * clock, so a 02:14 read off a chart is the 02:14 that is entered (F20, FR46, AC64).
 */
export function toLocalInput(iso: string, zone?: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  if (zone !== undefined) return wallClockIn(zone, at) ?? toLocalInput(iso);

  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
    `T${pad(at.getHours())}:${pad(at.getMinutes())}`
  );
}

/**
 * The reverse. Empty or unparseable returns null rather than a guess — a half-typed
 * date must not silently become an instant somewhere in 1970.
 */
export function fromLocalInput(value: string, zone?: string): string | null {
  if (!value) return null;
  if (zone !== undefined) return instantIn(zone, value) ?? fromLocalInput(value);
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? null : at.toISOString();
}

const MS_PER_MINUTE = 60_000;
const WALL_CLOCK_SEPARATOR = " ";
const WALL_CLOCK = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;

/** The wall clock `zone` shows at an instant, as an input's text; null for a zone not known. */
function wallClockIn(zone: string, at: Date): string | null {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(at);
    const part = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((found) => found.type === type)?.value ?? "";
    return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
  } catch {
    return null;
  }
}

/** How far ahead of UTC the zone's clock is at that instant, in minutes. */
function offsetMinutes(zone: string, at: Date): number | null {
  const shown = wallClockIn(zone, at);
  const match = shown === null ? null : WALL_CLOCK.exec(shown);
  if (match === null) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  const asUtc = Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1, hour ?? 0, minute ?? 0);
  return Math.round(
    (asUtc - Math.floor(at.getTime() / MS_PER_MINUTE) * MS_PER_MINUTE) / MS_PER_MINUTE,
  );
}

/**
 * The instant at which `zone`'s wall clock reads this text. Two passes, so a time near a
 * clock change lands on the side the offset at the answer says.
 */
function instantIn(zone: string, value: string): string | null {
  const match = WALL_CLOCK.exec(value);
  if (match === null) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  const asUtc = Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1, hour ?? 0, minute ?? 0);
  // `Date.UTC` rolls 13/45 over into a real date; text that is not one is not an instant.
  if (new Date(asUtc).toISOString().slice(0, 16) !== value.replace(WALL_CLOCK_SEPARATOR, "T")) {
    return null;
  }
  const first = offsetMinutes(zone, new Date(asUtc));
  if (first === null) return null;
  const guess = asUtc - first * MS_PER_MINUTE;
  const second = offsetMinutes(zone, new Date(guess)) ?? first;
  return new Date(asUtc - second * MS_PER_MINUTE).toISOString();
}

/**
 * Whether a pair is usable as a window: both parse, and they are in order.
 *
 * Equal instants are refused too. A window of zero length reads as a mistake on every
 * screen — an empty list that blames the filters — rather than as the empty answer it
 * literally is.
 */
export function isUsableRange(from: string | null, to: string | null): boolean {
  if (!from || !to) return false;
  const start = Date.parse(from);
  const end = Date.parse(to);
  return Number.isFinite(start) && Number.isFinite(end) && start < end;
}

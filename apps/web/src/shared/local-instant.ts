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

/** `2026-09-08T14:00:00Z` in Berlin becomes `2026-09-08T16:00`, which is what Berlin sees. */
export function toLocalInput(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";

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
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? null : at.toISOString();
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

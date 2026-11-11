import { useRouter } from "@tanstack/react-router";
import { useCallback, useMemo, useSyncExternalStore } from "react";

import { browserTimeZone, formatZonedTime, type ZonedOptions, zoneLabel } from "./format/zoned";

/**
 * A window carried in the address, so a link from a finding opens the right minutes and a
 * copied link opens them again (F3 FR16, AC21; design, *Links, and the window in the URL*).
 *
 * `?from=&to=&project=&finding=` on the six screens a finding links to. It is **read in
 * preference to the stored window and never written to it**: following a link must not
 * change the window a person chose for every other screen. Moving away through the
 * navigation ends it, because the navigation carries no such keys.
 *
 * `finding` grants nothing: a destination only uses it to link back. Each value is
 * checked here, so a hand-edited address shows the person's own window rather than an
 * error, and only an identifier-shaped `project` or `finding` is ever kept.
 */

export interface UrlWindow {
  from: string;
  to: string;
  /** The project the minutes belong to; selected on arrival. */
  project?: string;
  /** The finding the link came from, for "Back to the finding". */
  finding?: string;
}

/** The address keys this window travels as. */
export const URL_WINDOW_KEYS = ["from", "to", "project", "finding"] as const;

/**
 * The screens that read a window from the address (design, *Links*). Any other path
 * ignores it, so a stray `from` on a settings page changes nothing.
 */
const WINDOW_PATHS = ["/traces", "/logs", "/errors", "/infrastructure", "/releases"] as const;
const SERVICE_PATH = /^\/services\/[^/]+\/?$/;
const IDENTIFIER = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** A window longer than this is not one a finding links to; it is ignored. */
const LONGEST_WINDOW_MS = 31 * 24 * 60 * 60_000;

export function urlWindowApplies(pathname: string): boolean {
  return (
    WINDOW_PATHS.some((path) => pathname === path || pathname === `${path}/`) ||
    SERVICE_PATH.test(pathname)
  );
}

function instant(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > 40) return undefined;
  const at = Date.parse(value);
  return Number.isFinite(at) ? new Date(at).toISOString() : undefined;
}

function identifier(value: unknown): string | undefined {
  return typeof value === "string" && IDENTIFIER.test(value) ? value.toLowerCase() : undefined;
}

/** The address's window, or null when it holds none that can be read. */
export function parseUrlWindow(search: Record<string, unknown>): UrlWindow | null {
  const from = instant(search.from);
  const to = instant(search.to);
  if (from === undefined || to === undefined) return null;
  const span = Date.parse(to) - Date.parse(from);
  if (span <= 0 || span > LONGEST_WINDOW_MS) return null;
  const window: UrlWindow = { from, to };
  const project = identifier(search.project);
  const finding = identifier(search.finding);
  if (project) window.project = project;
  if (finding) window.finding = finding;
  return window;
}

/** The window as address keys, for a link or a `validateSearch`. */
export function urlWindowSearch(window: UrlWindow | null): Record<string, string> {
  if (window === null) return {};
  return {
    from: window.from,
    to: window.to,
    ...(window.project ? { project: window.project } : {}),
    ...(window.finding ? { finding: window.finding } : {}),
  };
}

/**
 * What a screen keeps of its address when it rewrites the rest: the window, checked.
 * Each of the six routes joins this into its `validateSearch`, and each screen spreads it
 * into the search it writes, so changing a filter keeps the window (AC21).
 */
export function keepUrlWindow(search: Record<string, unknown>): Record<string, string> {
  return urlWindowSearch(parseUrlWindow(search));
}

/** The address with the window removed: "Use my own window". */
export function withoutUrlWindow(search: Record<string, unknown>): Record<string, unknown> {
  const kept: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(search)) {
    if (!(URL_WINDOW_KEYS as readonly string[]).includes(key)) kept[key] = value;
  }
  return kept;
}

const noop = () => () => {};
/** Between a path and its search in one snapshot string, which neither can hold. */
const SEPARATOR = "\u0000";

/**
 * The window in the current address, on a screen that reads one; null elsewhere, and
 * outside a router (a component rendered on its own in a test has no address).
 */
export function useUrlWindow(): UrlWindow | null {
  const router = useRouter({ warn: false });
  const subscribe = useCallback(
    (listener: () => void) => (router ? router.history.subscribe(listener) : noop()),
    [router],
  );
  const address = useSyncExternalStore(subscribe, () =>
    router
      ? `${router.history.location.pathname}${SEPARATOR}${router.history.location.search}`
      : null,
  );
  const parseSearch = router?.options.parseSearch;
  return useMemo(() => {
    if (address === null || parseSearch === undefined) return null;
    const [pathname = "", search = ""] = address.split(SEPARATOR);
    if (!urlWindowApplies(pathname)) return null;
    return parseUrlWindow(parseSearch(search) as Record<string, unknown>);
  }, [address, parseSearch]);
}

/**
 * The words under the window control while the address holds a window (FR16): its
 * minutes with their zone, and whose window it is. "Back to the finding" shows only
 * with a valid `finding` (AC21).
 */
export function urlWindowLine(window: UrlWindow, { zone, now }: ZonedOptions = {}): string {
  const from = formatZonedTime(window.from, { zone, now });
  const to = formatZonedTime(window.to, { zone, now });
  const label = zoneLabel(zone ?? browserTimeZone(), new Date(window.to));
  // One zone name for the pair, as "13:47 to 14:20 IST" reads.
  const start =
    from.endsWith(` ${label}`) && to.endsWith(` ${label}`)
      ? from.slice(0, -label.length - 1)
      : from;
  return window.finding
    ? `Showing this finding's window, ${start} to ${to}`
    : `Showing ${start} to ${to}`;
}

/**
 * The two selections every signed-in screen is read against: which project, and over
 * what window.
 *
 * A module store rather than component state, because the top bar sets them and the
 * screens read them, and those live on opposite sides of the app/feature boundary —
 * a feature may not import app/, so the shared layer has to hold the value.
 */

import { useEffect, useState, useSyncExternalStore } from "react";

import { refetchIntervalFor, useRefreshPreference } from "./refresh";

export interface TimeRange {
  id: string;
  label: string;
  minutes: number;
}

export const TIME_RANGES: readonly TimeRange[] = [
  { id: "15m", label: "15 minutes", minutes: 15 },
  { id: "1h", label: "hour", minutes: 60 },
  { id: "6h", label: "6 hours", minutes: 360 },
  { id: "24h", label: "24 hours", minutes: 1440 },
  { id: "7d", label: "7 days", minutes: 10_080 },
];

const DEFAULT_RANGE_ID = "1h";

/**
 * `to` is "now", and a value that changes every millisecond changes the query key every
 * render. Rounding it to a bucket keeps the key stable between ticks, which is the
 * difference between a cached window and an endless refetch loop.
 *
 * It rounds *up*. Rounding down excludes everything that arrived since the last bucket
 * boundary — which is precisely the newest data, the reason anyone opened the screen.
 * A `to` slightly in the future costs nothing: there is no telemetry from the future.
 */
const WINDOW_BUCKET_MS = 10_000;
const MS_PER_MINUTE = 60_000;

/**
 * What window a reader asked for, which is not the same as the window a read uses.
 *
 * **A relative choice resolves to a different pair of instants every few seconds; an
 * absolute one never moves.** Keeping the *choice* rather than the resolved instants is
 * what lets a cache key stay still while the window underneath it advances — see
 * `windowCacheKey`. It is also the difference between "the last hour" and "14:00 to
 * 15:00 last Tuesday", which a customer needs and could not ask for until now: the
 * relative options stop at seven days while traces are retained for thirty and metrics
 * for over a year, so most of what is paid for was unreachable from any screen.
 */
export type WindowChoice =
  | { kind: "relative"; rangeId: string }
  | { kind: "absolute"; from: string; to: string };

interface Selection {
  projectId?: string;
  rangeId: string;
  /** The window the whole console reads against, unless a screen overrides it. */
  window: WindowChoice;
}

export const SELECTION_STORAGE_KEY = "nexovue.selection";

function readStored(): Selection {
  const fresh: Selection = {
    rangeId: DEFAULT_RANGE_ID,
    window: { kind: "relative", rangeId: DEFAULT_RANGE_ID },
  };
  try {
    const stored = localStorage.getItem(SELECTION_STORAGE_KEY);
    if (!stored) return fresh;
    const parsed: unknown = JSON.parse(stored);
    if (typeof parsed !== "object" || parsed === null) return fresh;

    const { projectId, window: windowChoice } = parsed as Partial<Selection>;
    return {
      projectId: typeof projectId === "string" ? projectId : undefined,
      rangeId: windowChoice?.kind === "relative" ? windowChoice.rangeId : DEFAULT_RANGE_ID,
      window: validWindowChoice(windowChoice) ?? fresh.window,
    };
  } catch {
    return fresh;
  }
}

/**
 * Refuses a remembered value this build cannot honour.
 *
 * A range id can disappear between releases, and an absolute pair can be written by
 * anything with access to the key. Neither should leave the console unable to read.
 */
function validWindowChoice(choice: WindowChoice | undefined): WindowChoice | undefined {
  if (choice?.kind === "relative") {
    return TIME_RANGES.some((range) => range.id === choice.rangeId) ? choice : undefined;
  }
  if (choice?.kind === "absolute") {
    const from = Date.parse(choice.from);
    const to = Date.parse(choice.to);
    return Number.isFinite(from) && Number.isFinite(to) && from < to ? choice : undefined;
  }
  return undefined;
}

let selection: Selection = readStored();
const listeners = new Set<() => void>();

function remember() {
  try {
    localStorage.setItem(
      SELECTION_STORAGE_KEY,
      JSON.stringify({ projectId: selection.projectId, window: selection.window }),
    );
  } catch {
    // It still applies to this tab; it just will not survive a reload.
  }
}

function publish() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setSelectedProjectId(projectId: string): void {
  if (selection.projectId === projectId) return;
  selection = { ...selection, projectId };
  remember();
  publish();
}

export function setSelectedRangeId(rangeId: string): void {
  if (selection.rangeId === rangeId && selection.window.kind === "relative") return;
  selection = { ...selection, rangeId, window: { kind: "relative", rangeId } };
  remember();
  publish();
}

/**
 * Pin the console to a fixed pair of instants — the incident, rather than "recently".
 *
 * The relative range is kept beside it, so leaving an absolute window returns a reader
 * to the one they were on rather than to a default they did not choose.
 */
export function setAbsoluteWindow(from: string, to: string): void {
  const choice = validWindowChoice({ kind: "absolute", from, to });
  if (!choice) return;
  selection = { ...selection, window: choice };
  remember();
  publish();
}

export function clearAbsoluteWindow(): void {
  if (selection.window.kind === "relative") return;
  selection = { ...selection, window: { kind: "relative", rangeId: selection.rangeId } };
  remember();
  publish();
}

export function useSelection(): Selection {
  return useSyncExternalStore(subscribe, () => selection);
}

/**
 * What a query key should carry for a window, and the whole reason this type exists.
 *
 * **A resolved window cannot go in a cache key.** `to` is "now", so a key holding it
 * changes every ten seconds, and a changed key is a *different query* with no cached
 * data — which means `isPending`, which means the screen renders its skeleton. Every
 * refresh was a cold load for that reason: on a fast connection a flicker, on a
 * customer's a full teardown of the screen, every ten seconds, for ever.
 *
 * The choice is stable where the window is not, so the same query refetches in the
 * background and the rows a reader is looking at stay on screen.
 */
export function windowCacheKey(choice: WindowChoice): string {
  return choice.kind === "relative" ? `rel:${choice.rangeId}` : `abs:${choice.from}/${choice.to}`;
}

/** The instants to read against, resolved at the moment of asking. */
export function resolveChoice(choice: WindowChoice, now = Date.now()): TimeWindow {
  return choice.kind === "absolute"
    ? { from: choice.from, to: choice.to }
    : resolveWindow(choice.rangeId, now);
}

/** The words for a window, which differ in kind rather than in length. */
export function describeChoice(choice: WindowChoice): string {
  return choice.kind === "relative"
    ? `last ${timeRangeById(choice.rangeId).label}`
    : `${new Date(choice.from).toLocaleString()} to ${new Date(choice.to).toLocaleString()}`;
}

export function timeRangeById(rangeId: string): TimeRange {
  return TIME_RANGES.find((range) => range.id === rangeId) ?? TIME_RANGES[1];
}

export interface TimeWindow {
  from: string;
  to: string;
}

export function resolveWindow(rangeId: string, now = Date.now()): TimeWindow {
  const bucketedNow = Math.ceil(now / WINDOW_BUCKET_MS) * WINDOW_BUCKET_MS;
  const span = timeRangeById(rangeId).minutes * MS_PER_MINUTE;

  return {
    from: new Date(bucketedNow - span).toISOString(),
    to: new Date(bucketedNow).toISOString(),
  };
}

/**
 * The window a live screen reads against, advancing one bucket at a time.
 *
 * `resolveWindow` alone is a snapshot: computed once, it pins the screen to the moment
 * it mounted, and a list captioned "newest first" quietly stops being either. The tick
 * is what makes the window move, and moving the window is what makes the query key
 * change, which is what refetches.
 *
 * It stops while the tab is hidden — nobody is reading, and each tick costs a request.
 */
/**
 * The same length of time, immediately before this window.
 *
 * A comparison is only fair against an equivalent span: an hour is compared with
 * the hour before it, never with "yesterday" or a fixed offset. Derived from the
 * window itself rather than from the range id, so a window someone narrowed by
 * hand is compared with its own predecessor.
 */
export function previousWindow(timeWindow: TimeWindow): TimeWindow {
  const from = Date.parse(timeWindow.from);
  const to = Date.parse(timeWindow.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return timeWindow;

  const span = to - from;
  return { from: new Date(from - span).toISOString(), to: timeWindow.from };
}

/**
 * The window a live screen reads against, and what a query should do about it.
 *
 * **It is a `TimeWindow` and carries a `key` beside it**, because those two answer
 * different questions and were conflated until now. `from`/`to` are what a *read* asks
 * for and they advance; `key` is what a *cache* is keyed on and it must not. Putting the
 * advancing pair in a query key made every tick a different query with no cached data —
 * `isPending`, and the screen renders its skeleton. Every refresh was a cold load.
 *
 * The tick stops when nothing can come of it: while the tab is hidden nobody is reading,
 * while paused the reader asked it to, and an absolute window has no newer instant to
 * advance to. Refresh is then driven by `refetchInterval` rather than by the key
 * changing underneath the screen.
 */
export interface LiveWindow extends TimeWindow {
  /** Stable while `from` and `to` advance. See `windowCacheKey`. */
  key: string;
  /** What a query should pass as `refetchInterval`: milliseconds, or `false`. */
  refetchInterval: number | false;
  isAbsolute: boolean;
}

/**
 * What a query key should carry for a window.
 *
 * Accepts a plain `TimeWindow` so a caller that resolved its own instants — a test, or a
 * read that is not live — still gets a key rather than a type error. A `LiveWindow` hands
 * back the stable one, which is the whole point; anything else falls back to the instants
 * it has, which is exactly as stable as that caller is.
 */
export function windowKeyOf(window: TimeWindow | LiveWindow): string {
  return "key" in window ? window.key : `${window.from}/${window.to}`;
}

/**
 * The options every live read shares.
 *
 * Refresh is driven from here rather than by the key changing, which is the correction:
 * a key that moved made each tick a different query with no data behind it, and the
 * screen fell back to its skeleton. A stable key plus an interval is the same freshness
 * without the teardown.
 */
export function liveOptions(window: TimeWindow | LiveWindow): { refetchInterval: number | false } {
  return { refetchInterval: "refetchInterval" in window ? window.refetchInterval : false };
}

export function useLiveWindow(): LiveWindow {
  const { window: choice } = useSelection();
  const preference = useRefreshPreference();
  const [now, setNow] = useState(() => Date.now());

  const isAbsolute = choice.kind === "absolute";
  const refetchInterval = refetchIntervalFor(preference, { windowIsAbsolute: isAbsolute });
  const still = isAbsolute || preference.paused;

  useEffect(() => {
    if (still) return;

    const advance = () => setNow(Date.now());
    const tick = setInterval(() => {
      if (document.visibilityState === "visible") advance();
    }, WINDOW_BUCKET_MS);

    // Catch up immediately on return, rather than after up to one more bucket.
    document.addEventListener("visibilitychange", advance);
    return () => {
      clearInterval(tick);
      document.removeEventListener("visibilitychange", advance);
    };
  }, [still]);

  const resolved = resolveChoice(choice, now);
  return { ...resolved, key: windowCacheKey(choice), refetchInterval, isAbsolute };
}

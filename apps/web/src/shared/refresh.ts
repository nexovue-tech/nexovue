import { useEffect, useSyncExternalStore } from "react";

/**
 * How often a live screen re-reads, and whether it re-reads at all.
 *
 * **A screen nobody can hold still is a screen nobody can study.** Reading a row
 * carefully, taking a screenshot for an incident channel, or turning a laptop towards a
 * colleague all fight a window that moves on its own. Pausing is the whole point; the
 * interval is what makes the same console work for an incident and for a wall display.
 *
 * A module store rather than component state, for the reason `selection.ts` gives: the
 * top bar sets it and every screen reads it, and a feature may not import `app/`.
 *
 * **Paused is not a preference like the others.** A paused screen shows numbers that
 * were true a while ago, and during an incident that is how somebody concludes a
 * service recovered when it did not. Whatever renders this has to say so loudly — the
 * same rule AC18 states for the Findings feed, where "we could not read it" must never
 * look like "nothing is wrong".
 */

export const REFRESH_STORAGE_KEY = "nexovue.refresh";

export interface RefreshInterval {
  id: string;
  label: string;
  ms: number;
}

/**
 * Five is the floor on purpose. Below it a columnar read has not finished before the
 * next one starts, and the telemetry tier's concurrency cap starts refusing reads that
 * the customer did nothing to deserve.
 */
export const REFRESH_INTERVALS: readonly RefreshInterval[] = [
  { id: "5s", label: "5 seconds", ms: 5_000 },
  { id: "10s", label: "10 seconds", ms: 10_000 },
  { id: "30s", label: "30 seconds", ms: 30_000 },
  { id: "1m", label: "1 minute", ms: 60_000 },
  { id: "5m", label: "5 minutes", ms: 300_000 },
];

const DEFAULT_INTERVAL_ID = "10s";

export interface RefreshPreference {
  paused: boolean;
  intervalId: string;
}

const listeners = new Set<() => void>();

export function intervalById(intervalId: string): RefreshInterval {
  return REFRESH_INTERVALS.find((option) => option.id === intervalId) ?? REFRESH_INTERVALS[1];
}

function readStored(): RefreshPreference {
  try {
    const stored = localStorage.getItem(REFRESH_STORAGE_KEY);
    if (!stored) return { paused: false, intervalId: DEFAULT_INTERVAL_ID };
    const parsed: unknown = JSON.parse(stored);
    if (typeof parsed !== "object" || parsed === null) {
      return { paused: false, intervalId: DEFAULT_INTERVAL_ID };
    }
    const { intervalId } = parsed as Partial<RefreshPreference>;
    return {
      // **Paused is deliberately not remembered.** Somebody who paused to read a row
      // yesterday should not open the console tomorrow to numbers that never move and
      // no memory of why. The interval is a preference; a pause is a moment.
      paused: false,
      intervalId:
        typeof intervalId === "string" && REFRESH_INTERVALS.some((o) => o.id === intervalId)
          ? intervalId
          : DEFAULT_INTERVAL_ID,
    };
  } catch {
    return { paused: false, intervalId: DEFAULT_INTERVAL_ID };
  }
}

let preference: RefreshPreference = readStored();

function publish() {
  for (const listener of listeners) listener();
}

function remember() {
  try {
    localStorage.setItem(
      REFRESH_STORAGE_KEY,
      JSON.stringify({ intervalId: preference.intervalId }),
    );
  } catch {
    // It still applies to this tab; it just will not survive a reload.
  }
}

export function setPaused(paused: boolean): void {
  if (preference.paused === paused) return;
  preference = { ...preference, paused };
  publish();
}

export function setRefreshIntervalId(intervalId: string): void {
  if (preference.intervalId === intervalId) return;
  preference = { ...preference, intervalId };
  remember();
  publish();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useRefreshPreference(): RefreshPreference {
  return useSyncExternalStore(subscribe, () => preference);
}

/**
 * What a query should use as its `refetchInterval`: a number, or `false` for "do not".
 *
 * `false` rather than a very large number, because the two are not the same thing to
 * TanStack Query and only one of them actually stops.
 */
export function refetchIntervalFor(
  preferenceNow: RefreshPreference,
  options: { windowIsAbsolute: boolean },
): number | false {
  // **An absolute window ends polling by itself.** Nothing new can arrive in a period
  // that closed last Tuesday, so asking again is a request the customer pays for and
  // nobody reads. It is not a preference and is deliberately not overridable.
  if (options.windowIsAbsolute) return false;
  if (preferenceNow.paused) return false;
  return intervalById(preferenceNow.intervalId).ms;
}

/* ------------------------------------------------------- screens that stream */

/**
 * A screen whose live data arrives over a socket rather than by asking again.
 *
 * **A poll control on a streaming screen is a control over nothing.** Logs are
 * tailed: lines arrive as they are written, and the interval picker beside them
 * governed only a facet count nobody was watching. Two refresh controls on one
 * screen, one of which does not refresh what the screen is showing, is worse
 * than either alone — it invites somebody to set five seconds and conclude the
 * tail is slow.
 *
 * So a screen that streams says so, and the one control in the top bar shows
 * that stream's state instead of an interval. Pause stays in the same place on
 * every screen, which is the point of having put it there.
 */
export type StreamState = "idle" | "connecting" | "live" | "paused" | "failed";

/**
 * A screen with nothing live on it at all.
 *
 * The settings screens read a name, a plan and a list of keys. None of it
 * arrives on its own, and an interval picker over them is the same defect the
 * logs screen had in the other direction: a control that governs nothing, read
 * by a customer as a promise that the screen is watching something.
 */
export const NOTHING_LIVE = "nothing-live" as const;

export interface LiveStream {
  state: StreamState;
  pause: () => void;
  resume: () => void;
}

let stream: LiveStream | typeof NOTHING_LIVE | undefined;
const streamListeners = new Set<() => void>();

function subscribeStream(listener: () => void): () => void {
  streamListeners.add(listener);
  return () => streamListeners.delete(listener);
}

/** Declared by the screen while it is mounted, and withdrawn when it leaves. */
export function declareStream(next: LiveStream | typeof NOTHING_LIVE | undefined): void {
  stream = next;
  for (const listener of streamListeners) listener();
}

/** What is declared right now. Separate from the hook so a test needs no tree. */
export function readLiveStream(): LiveStream | typeof NOTHING_LIVE | undefined {
  return stream;
}

export function useLiveStream(): LiveStream | typeof NOTHING_LIVE | undefined {
  return useSyncExternalStore(subscribeStream, readLiveStream);
}

/**
 * Declare this screen's stream for as long as it is on screen.
 *
 * Takes the pieces rather than an object: the caller rebuilds its tail object on
 * every render, and an effect keyed on that would re-declare forever.
 */
export function useDeclaredStream(state: StreamState, pause: () => void, resume: () => void): void {
  useEffect(() => {
    declareStream({ state, pause, resume });
    return () => declareStream(undefined);
  }, [state, pause, resume]);
}

/** Declared by a screen that polls nothing, for as long as it is on screen. */
export function useNothingLive(): void {
  useEffect(() => {
    declareStream(NOTHING_LIVE);
    return () => declareStream(undefined);
  }, []);
}

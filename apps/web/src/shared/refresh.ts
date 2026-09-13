import { useSyncExternalStore } from "react";

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

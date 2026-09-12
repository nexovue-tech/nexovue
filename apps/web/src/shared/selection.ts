/**
 * The two selections every signed-in screen is read against: which project, and over
 * what window.
 *
 * A module store rather than component state, because the top bar sets them and the
 * screens read them, and those live on opposite sides of the app/feature boundary —
 * a feature may not import app/, so the shared layer has to hold the value.
 */

import { useEffect, useState, useSyncExternalStore } from "react";

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

const DEFAULT_RANGE_ID = "7d"; // for now tesing 

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

interface Selection {
  projectId?: string;
  rangeId: string;
}

let selection: Selection = { rangeId: DEFAULT_RANGE_ID };
const listeners = new Set<() => void>();

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
  publish();
}

export function setSelectedRangeId(rangeId: string): void {
  if (selection.rangeId === rangeId) return;
  selection = { ...selection, rangeId };
  publish();
}

export function useSelection(): Selection {
  return useSyncExternalStore(subscribe, () => selection);
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
export function useLiveWindow(rangeId: string): TimeWindow {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
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
  }, []);

  return resolveWindow(rangeId, now);
}

/**
 * The two selections every signed-in screen is read against: which project, and over
 * what window.
 *
 * A module store rather than component state, because the top bar sets them and the
 * screens read them, and those live on opposite sides of the app/feature boundary —
 * a feature may not import app/, so the shared layer has to hold the value.
 */

import { useSyncExternalStore } from "react";

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
 * render. Flooring it to a bucket keeps the key stable between ticks, which is the
 * difference between a cached window and an endless refetch loop.
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
  const bucketedNow = Math.floor(now / WINDOW_BUCKET_MS) * WINDOW_BUCKET_MS;
  const span = timeRangeById(rangeId).minutes * MS_PER_MINUTE;

  return {
    from: new Date(bucketedNow - span).toISOString(),
    to: new Date(bucketedNow).toISOString(),
  };
}

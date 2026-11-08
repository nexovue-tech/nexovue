import type { PanelReadResponse, PanelResult } from "./api";
import { transient } from "./panel-words";

/**
 * A panel's last good line, kept briefly after a failed read (F20, FR31, BR5, D29).
 *
 * On a live window a failed panel keeps its last line for one refresh interval, at most
 * five minutes, under a filled warning strip; then the line goes and only the sentence
 * stays. One blip never blanks a page, and an old line never passes for a current one.
 * A change of service, project or window starts again, because the page keys this by
 * its query.
 */

export const KEEP_AT_MOST_MS = 5 * 60_000;

export type OkResult = Extract<PanelResult, { state: "ok" }>;

export interface KeptReading {
  reading: OkResult;
  /** When reads first failed after it, or null while it is current. */
  failingSince: number | null;
}

export type KeptReadings = Record<string, KeptReading>;

export function nextKept(
  previous: KeptReadings,
  answer: PanelReadResponse,
  now: number,
): KeptReadings {
  const kept: KeptReadings = {};
  for (const [panelId, result] of Object.entries(answer.panels)) {
    if (result.state === "ok") {
      kept[panelId] = { reading: result, failingSince: null };
    } else if (transient(result) && previous[panelId] !== undefined) {
      const earlier = previous[panelId];
      kept[panelId] = { ...earlier, failingSince: earlier.failingSince ?? now };
    }
  }
  return kept;
}

/** Every panel failing at once, as a whole-page outage keeps them (AC98). */
export function allFailing(previous: KeptReadings, now: number): KeptReadings {
  return Object.fromEntries(
    Object.entries(previous).map(([panelId, earlier]) => [
      panelId,
      { ...earlier, failingSince: earlier.failingSince ?? now },
    ]),
  );
}

function limitOf(intervalMs: number | false): number {
  return intervalMs === false ? KEEP_AT_MOST_MS : Math.min(intervalMs, KEEP_AT_MOST_MS);
}

/** When the next kept line must go, so the page can drop it then, refresh or none. */
export function nextExpiry(
  kept: KeptReadings,
  intervalMs: number | false,
  now: number,
): number | undefined {
  const expiries = Object.values(kept)
    .flatMap((reading) =>
      reading.failingSince === null ? [] : [reading.failingSince + limitOf(intervalMs)],
    )
    .filter((expiry) => expiry >= now);
  return expiries.length > 0 ? Math.min(...expiries) + 1 : undefined;
}

/** The old line to draw under the strip, while it may still be shown. */
export function keptLine(
  kept: KeptReading | undefined,
  intervalMs: number | false,
  now: number,
): OkResult | undefined {
  if (kept === undefined || kept.failingSince === null) return undefined;
  return now - kept.failingSince <= limitOf(intervalMs) ? kept.reading : undefined;
}

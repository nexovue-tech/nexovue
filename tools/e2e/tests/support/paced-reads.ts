/**
 * A walk through every console screen, paced to the reads one person may make.
 *
 * The API counts a signed-in person's reads a minute (`ratelimit/policy.py`): 300 for most
 * paths, 180 for telemetry. A screen opens with about a dozen reads, so a spec visiting the
 * console's ~40 screens back to back spends a minute's allowance in half of one; the API
 * then answers 429, the session read fails, and the screen loses its signed-in chrome. That
 * is the limit working, not a defect, and it must not be widened for us (`auth-quota.ts`).
 * So the walk waits, before each screen, until the last minute's reads leave room for one.
 */

import { setTimeout as sleep } from "node:timers/promises";
import type { Page } from "@playwright/test";

/** TELEMETRY_TIER's per-person quota, the tighter of the two a screen's reads fall under. */
const READS_A_MINUTE = 180;
const WINDOW_MS = 60_000;
/** Room for one screen's opening reads and the polling that runs while it is open. */
const ONE_SCREEN = 40;

/**
 * Starts counting `page`'s API requests and returns the wait to call before each screen.
 * Every request under `/api/` is counted, whichever tier it falls in, so the count can only
 * err towards waiting.
 */
export function paceReads(page: Page): () => Promise<void> {
  const sent: number[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) sent.push(Date.now());
  });
  return async () => {
    for (;;) {
      const now = Date.now();
      while (sent.length > 0 && sent[0] <= now - WINDOW_MS) sent.shift();
      if (sent.length <= READS_A_MINUTE - ONE_SCREEN) return;
      // The oldest read leaves the window first; a margin so the server agrees it has.
      await sleep(sent[0] + WINDOW_MS - now + 250);
    }
  };
}

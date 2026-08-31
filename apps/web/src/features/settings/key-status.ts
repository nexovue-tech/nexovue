/**
 * What a key's `last_used_at` means for the decision in front of the customer.
 *
 * The standard rotation is: create the new key, deploy it, confirm nothing is
 * using the old one, then revoke it. The middle step is the one people skip,
 * because without a reading they are guessing — so this file turns the timestamp
 * into the four answers that step actually has, and the screen shows the answer
 * rather than the raw time.
 *
 * The timestamp is deliberately coarse. The receiver records a use only when it
 * re-reads the key from the control plane, which is at most once per credential
 * cache period, so a key in constant use reads as "seconds ago" and never as
 * "now". Both thresholds here sit far above that resolution, so the coarseness
 * cannot move a key between states.
 */

import type { ApiKeySummary } from "./api";

const MS_PER_MINUTE = 60_000;
const MS_PER_HOUR = 60 * MS_PER_MINUTE;

/**
 * Under an hour of silence is not evidence of anything: a low-traffic service
 * batches, a cron job runs hourly, a staging environment sleeps overnight.
 */
export const IN_USE_WITHIN_MS = MS_PER_HOUR;

/**
 * A full day with no traffic is the reading the rotation guidance asks for
 * before revoking. It is the shorter end of the published range — 24 hours to
 * 7 days — because we can see actual traffic rather than having to assume it.
 */
export const QUIET_ENOUGH_TO_REVOKE_MS = 24 * MS_PER_HOUR;

export type KeyTraffic =
  /** Nothing has ever authenticated with this key. */
  | "never-used"
  /** Seen within the hour. Revoking this stops something that is running. */
  | "in-use"
  /** Quiet, but not for long enough to call it deployed-away-from. */
  | "settling"
  /** A full day of silence. This is what "verified no traffic" looks like. */
  | "quiet";

export interface KeyStatus {
  traffic: KeyTraffic;
  revoked: boolean;
  /** Milliseconds since the last recorded use; undefined when never used. */
  silentForMs?: number;
}

export function keyStatus(key: ApiKeySummary, now: number): KeyStatus {
  const revoked = key.revoked_at !== null;
  if (key.last_used_at === null) return { traffic: "never-used", revoked };

  const lastUsed = Date.parse(key.last_used_at);
  if (Number.isNaN(lastUsed)) return { traffic: "never-used", revoked };

  // Clamped at zero: a receiver's clock can sit slightly ahead of the browser's,
  // and a negative silence would read as a key used in the future.
  const silentForMs = Math.max(0, now - lastUsed);
  if (silentForMs < IN_USE_WITHIN_MS) return { traffic: "in-use", revoked, silentForMs };
  if (silentForMs < QUIET_ENOUGH_TO_REVOKE_MS) return { traffic: "settling", revoked, silentForMs };
  return { traffic: "quiet", revoked, silentForMs };
}

/**
 * Whether the old key can be withdrawn without taking something down with it.
 *
 * A key that has never authenticated qualifies for the same reason a long-quiet
 * one does: nothing has ever depended on it.
 */
export function isVerifiedQuiet(status: KeyStatus): boolean {
  return status.traffic === "quiet" || status.traffic === "never-used";
}

/** The short label beside a key. Written for the decision, not as a status word. */
export const TRAFFIC_LABEL: Record<KeyTraffic, string> = {
  "never-used": "Never used",
  "in-use": "In use",
  settling: "Going quiet",
  quiet: "No traffic",
};

/**
 * The sentence under the label. It says what revoking this key would do, because
 * that is the question being asked of it.
 */
export function describeTraffic(status: KeyStatus): string {
  if (status.revoked) {
    return "Withdrawn. The receiver stopped accepting it within a minute of revocation.";
  }
  switch (status.traffic) {
    case "never-used":
      return "Nothing has ever authenticated with this key. Revoking it stops nothing.";
    case "in-use":
      return "Something authenticated with this key in the last hour. Revoking it stops that service sending telemetry.";
    case "settling":
      return "No traffic for a while, but not long enough to be sure. Give it a full day of silence before revoking.";
    case "quiet":
      return "No traffic for over a day. Nothing is sending with this key, so it is safe to revoke.";
  }
}

/**
 * Live keys first and newest first within each group, so the key somebody just
 * minted is at the top and the withdrawn ones do not push it down the page.
 */
export function orderedKeys(keys: readonly ApiKeySummary[]): ApiKeySummary[] {
  return [...keys].sort((left, right) => {
    const leftRevoked = left.revoked_at !== null;
    const rightRevoked = right.revoked_at !== null;
    if (leftRevoked !== rightRevoked) return leftRevoked ? 1 : -1;
    return Date.parse(right.created_at) - Date.parse(left.created_at);
  });
}

export function liveKeyCount(keys: readonly ApiKeySummary[]): number {
  return keys.filter((key) => key.revoked_at === null).length;
}

/** The name a rotation offers by default: the old key's, dated, so a list of
 * five keys still reads as a history rather than five identical rows. */
export function suggestedRotationName(key: ApiKeySummary, now: number): string {
  const date = new Date(now).toISOString().slice(0, 10);
  return `${key.name} (rotated ${date})`;
}

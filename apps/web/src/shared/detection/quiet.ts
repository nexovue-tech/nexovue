import type { Quieting } from "./api";

/**
 * How long a service can be quietened, as choices rather than a clock to type: a
 * snooze is at most 4 hours (D25), a mute at most 30 days (FR22). The API refuses
 * anything longer, so nothing here can offer it.
 */

export interface QuietChoice {
  id: string;
  label: string;
  minutes: number;
}

const HOUR = 60;
const DAY = 24 * HOUR;

export const QUIET_CHOICES: Record<Quieting, readonly QuietChoice[]> = {
  snooze: [
    { id: "30m", label: "30 minutes", minutes: 30 },
    { id: "1h", label: "1 hour", minutes: HOUR },
    { id: "2h", label: "2 hours", minutes: 2 * HOUR },
    { id: "4h", label: "4 hours", minutes: 4 * HOUR },
  ],
  mute: [
    { id: "1h", label: "1 hour", minutes: HOUR },
    { id: "1d", label: "1 day", minutes: DAY },
    { id: "7d", label: "7 days", minutes: 7 * DAY },
    { id: "30d", label: "30 days", minutes: 30 * DAY },
  ],
};

const MS_PER_MINUTE = 60_000;

/** The instant a choice ends, from now. */
export function quietUntil(choice: QuietChoice, now = Date.now()): string {
  return new Date(now + choice.minutes * MS_PER_MINUTE).toISOString();
}

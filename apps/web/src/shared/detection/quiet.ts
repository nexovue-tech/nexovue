import { formatZonedTime } from "../format/zoned";
import type { Quieting, ServiceControls } from "./api";

/**
 * How long a service can be quietened, as choices rather than a clock to type: a
 * snooze is at most 4 hours (D25), a mute at most 30 days (FR22). The choice is sent
 * as a length and the API's clock says when it ends, so a browser clock running ahead
 * cannot turn the longest choice into one the API refuses.
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

/** What arming a snooze or a mute will do, said before it is confirmed. */
export function quietEffect(kind: Quieting, service: string): string {
  return kind === "snooze"
    ? `Nothing is sent about ${service} until the snooze ends, and its minutes stay out of its normal. Members can snooze a service for 4 hours in any 24.`
    : `Nothing is sent about ${service} until the mute ends, nor afterwards for what it covered. Its findings are still recorded, under Muted.`;
}

/** What a snooze or a mute did, from the API's answer, on the project's clock. */
export function quietDone(
  kind: Quieting,
  service: string,
  controls: ServiceControls,
  zone?: string,
): string {
  if (kind === "snooze") {
    return controls.snoozed_until
      ? `${service} is snoozed until ${formatZonedTime(controls.snoozed_until, { zone })}.`
      : `The snooze on ${service} has ended.`;
  }
  return controls.muted_until
    ? `${service} is muted until ${formatZonedTime(controls.muted_until, { zone })}. Its findings are still recorded.`
    : `${service} is no longer muted.`;
}

/**
 * A window dragged on a chart, rounded out to whole steps (F20, FR53, T4.6).
 *
 * Colleagues zooming on one spike drag slightly different spans; rounded out, most of
 * them ask for the same window and share its scans. Whole minutes under an hour, whole
 * five minutes above (*guesses*, design *Reads in an incident*). Out, never in: the
 * moment a person dragged across stays inside what they get.
 */

const SECONDS_PER_MINUTE = 60;
const MINUTE_STEP_SECONDS = SECONDS_PER_MINUTE;
const FIVE_MINUTE_STEP_SECONDS = 5 * SECONDS_PER_MINUTE;
/** Spans under this round to whole minutes, longer ones to whole five minutes. */
const MINUTE_STEPS_BELOW_SECONDS = 60 * SECONDS_PER_MINUTE;
const MS_PER_SECOND = 1000;

export interface DraggedWindow {
  from: string;
  to: string;
}

/** The dragged span, in the epoch seconds a chart hands over, rounded out to its step. */
export function roundedOut(fromSeconds: number, toSeconds: number): DraggedWindow | undefined {
  if (!Number.isFinite(fromSeconds) || !Number.isFinite(toSeconds) || toSeconds <= fromSeconds) {
    return undefined;
  }
  const step =
    toSeconds - fromSeconds < MINUTE_STEPS_BELOW_SECONDS
      ? MINUTE_STEP_SECONDS
      : FIVE_MINUTE_STEP_SECONDS;
  const from = Math.floor(fromSeconds / step) * step;
  const to = Math.max(Math.ceil(toSeconds / step) * step, from + step);
  return {
    from: new Date(from * MS_PER_SECOND).toISOString(),
    to: new Date(to * MS_PER_SECOND).toISOString(),
  };
}

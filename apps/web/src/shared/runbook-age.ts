/**
 * How old a runbook, or a team's note, reads beside what cites it (F4 FR15, FR17, FR32). One
 * place, so the runbooks screen and the chat panel say the same age and turn to a warning tone
 * at the same day. Past `STALE_RUNBOOK_DAYS` (a guess, held equal to
 * `nexovue_platform.knowledge.STALE_RUNBOOK_DAYS` by its test) the words warn.
 */

export const STALE_RUNBOOK_DAYS = 180;
const MS_PER_DAY = 24 * 60 * 60 * 1000;

export interface Age {
  /** "written today", "1 day old", "12 days old"; an em dash for a date that cannot be read. */
  words: string;
  stale: boolean;
}

export function writtenAge(iso: string | null | undefined, now: number): Age {
  const at = iso ? Date.parse(iso) : Number.NaN;
  if (Number.isNaN(at)) return { words: "—", stale: false };
  const days = Math.max(0, Math.floor((now - at) / MS_PER_DAY));
  const words = days === 0 ? "written today" : `${days} ${days === 1 ? "day" : "days"} old`;
  return { words, stale: days > STALE_RUNBOOK_DAYS };
}

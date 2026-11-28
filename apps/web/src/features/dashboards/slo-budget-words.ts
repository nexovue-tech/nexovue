import { visibleEscapes } from "@/shared/format/visible-escapes";
import { formatZonedTime } from "@/shared/format/zoned";
import { allowedNoun, lastsFor, type SloState } from "@/shared/slo/words";

import type { SloBudget } from "./api";

/**
 * What a reliability-target panel says (T10.2d), from F19's own figures, in F19's words:
 * the state, what is still allowed, and while it is running out how fast and how long
 * what is left lasts. Pure, and `now` is passed, never read, so each line is tested on a
 * pinned clock. The AC13 word scan reads this file.
 */

/** F19's NFR7: older than this, a figure is Not up to date, worked out in the browser too. */
export const STALE_AFTER_MS = 15 * 60_000;

const grouped = new Intl.NumberFormat("en-GB");

/** The state as this browser sees it now: a figure the job stopped refreshing is stale. */
export function budgetState(budget: SloBudget, now: number): SloState {
  if (budget.state === "disabled") return budget.state;
  const last = budget.evaluated_minute ?? budget.created_at;
  return now - Date.parse(last) > STALE_AFTER_MS ? "not_up_to_date" : budget.state;
}

export interface BudgetWords {
  /** The state this browser sees, which the shared badge says and colours. */
  state: SloState;
  /** "Failures still allowed: 312 of 672." Never below zero (FR12). */
  allowance: string;
  /** While running out: the burn's rate, and how long what is left lasts when known. */
  burn: string | null;
  /** When it was last worked out, in the project's zone, or that it never was. */
  asOf: string;
}

export function budgetWords(budget: SloBudget, now: number): BudgetWords {
  const state = budgetState(budget, now);
  const at = budget.evaluated_minute
    ? formatZonedTime(budget.evaluated_minute, { zone: budget.zone, now })
    : null;
  return {
    state,
    allowance: `${budget.allowance_words}: ${grouped.format(Math.max(budget.figures.left, 0))} of ${grouped.format(budget.figures.allowed)}.`,
    burn: state === "running_out" ? burnLine(budget) : null,
    asOf:
      at === null
        ? "Not worked out yet."
        : `${state === "not_up_to_date" ? "Not up to date since" : "As of"} ${at}.`,
  };
}

/** "Using allowed failures 14 times faster than allowed. What is left lasts about 2 days." */
function burnLine(budget: SloBudget): string | null {
  const burn = budget.burn;
  if (burn === null || burn.resolving) return null;
  const rate = `Using ${allowedNoun(budget.kind)} ${Math.floor(burn.rate)} times faster than allowed.`;
  const lasts = lastsFor(burn.hours_left);
  return lasts === null ? rate : `${rate} ${lasts}`;
}

/** A panel that reads no target in the displayed project: the words claim only that (AC22). */
export function noTargetWords(name: string, projectName: string): string {
  return `No reliability target named ${visibleEscapes(name)} in ${visibleEscapes(projectName)}.`;
}

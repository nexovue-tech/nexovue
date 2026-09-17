import type { Urgency } from "./check-health";
import type { DisplayedState, Outcome, RunEvidence } from "./check-model";
import { OUTCOMES } from "./check-model";

/**
 * What a state or an outcome is called, and how loud it is.
 *
 * Health is the absence of colour. Down is the one state that gets a filled mark; a
 * degraded or misconfigured check gets a warning, because someone has to look at it but
 * the service may well be answering.
 */

export const STATE_WORDS: Record<DisplayedState, string> = {
  up: "up",
  degraded: "degraded",
  down: "down",
  misconfigured: "misconfigured",
  paused: "paused",
  maintenance: "in maintenance",
};

export const OUTCOME_WORDS: Record<Outcome, string> = {
  up: "passed",
  degraded: "passed slowly",
  down: "failed",
  misconfigured: "could not run as written",
  prober_error: "could not be run by us",
};

export function stateUrgency(state: DisplayedState | null): Urgency {
  if (state === "down") return "critical";
  if (state === "degraded" || state === "misconfigured") return "warning";
  return "none";
}

export function outcomeUrgency(outcome: string): Urgency {
  if (outcome === "down") return "critical";
  if (outcome === "degraded" || outcome === "misconfigured" || outcome === "prober_error")
    return "warning";
  return "none";
}

export function isOutcome(value: string): value is Outcome {
  // Not `in OUTCOME_WORDS`: that also answers true for "toString".
  return (OUTCOMES as readonly string[]).includes(value);
}

/** The one sentence a run is read as (principle 1). */
export function runSentence(evidence: RunEvidence): string {
  const where = evidence.location ? ` from ${evidence.location}` : "";
  const attempt = evidence.attempt > 1 ? ` on attempt ${evidence.attempt}` : "";
  const base = `The run ${OUTCOME_WORDS[evidence.outcome]}${where}${attempt}`;
  if (evidence.outcome === "up") return `${base}.`;
  return evidence.error ? `${base}: ${evidence.error}` : `${base}.`;
}

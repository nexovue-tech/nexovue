/**
 * When a service stops being healthy, and when it starts being urgent.
 *
 * **These two numbers are product policy, and they live here so they are findable.**
 * The mark was binary until 2026-09-14: one error in a hundred thousand requests drew
 * the same red dot as forty percent failing, so on a list of sixteen services with
 * twelve marked, the mark had stopped carrying information. A signal that fires too
 * often is one people learn to scan past, and then it fails to fire when it matters.
 *
 * **Fixed thresholds rather than a service's own baseline.** "Failing more than it
 * usually does" is the better question and it is the insight engine's to answer — it
 * needs history, and a screen that invented it would be guessing at normal. A fixed pair
 * is explainable, and the screen states it rather than leaving a reader to infer what a
 * colour means.
 *
 * Recorded as D7 in the feature's `status.md`. One pair to change.
 */

export type ServiceSeverity = "healthy" | "warning" | "critical";

/** A fraction, not a percentage: 0.01 is one request in a hundred. */
export const WARNING_ERROR_RATE = 0.01;
export const CRITICAL_ERROR_RATE = 0.05;

/** Said on the screen, so a reader knows what the mark claims. */
export const SEVERITY_LEGEND = "Marked over 1% failing, filled over 5%.";

/**
 * **A rate needs requests behind it before it means anything.** One error in the only
 * two requests a quiet service received is a 50% error rate and not an incident, and
 * marking it critical would put the loudest signal on the least evidence. Below this,
 * severity falls back to whether anything failed at all.
 */
export const MINIMUM_REQUESTS_FOR_A_RATE = 20;

export function serviceSeverity(metrics: {
  error_count: number;
  error_rate: number;
  request_count: number;
}): ServiceSeverity {
  if (metrics.error_count <= 0) return "healthy";
  if (metrics.request_count < MINIMUM_REQUESTS_FOR_A_RATE) return "warning";
  if (metrics.error_rate >= CRITICAL_ERROR_RATE) return "critical";
  return metrics.error_rate >= WARNING_ERROR_RATE ? "warning" : "healthy";
}

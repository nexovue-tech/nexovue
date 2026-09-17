import type { CheckResultSummary, CheckRun } from "./api";

/**
 * What the readings mean, decided once.
 *
 * Health is the absence of colour, so most urgency this module returns is "none".
 */

/** Ordered worst first. */
export type Urgency = "critical" | "warning" | "none";

/**
 * Let's Encrypt begins renewing at 30 days remaining and issues 90-day certificates, so
 * 30 days left is routine and colouring it would make the screen cry wolf once a month.
 * Under 21 days means a renewal window has already been missed; under 7 means the
 * outage is close enough to be someone's afternoon.
 */
const CERTIFICATE_WARNING_DAYS = 21;
const CERTIFICATE_CRITICAL_DAYS = 7;

export function certificateUrgency(daysRemaining: number): Urgency {
  if (daysRemaining <= CERTIFICATE_CRITICAL_DAYS) return "critical";
  if (daysRemaining <= CERTIFICATE_WARNING_DAYS) return "warning";
  return "none";
}

/**
 * Uptime to one decimal, because 99.96% and 100% are different facts and rounding to
 * whole percent hides every short outage a window this size can hold.
 */
export function formatUptime(check: CheckResultSummary): string {
  if (check.runs === 0) return "—";
  return `${check.uptime_percent.toFixed(1)}%`;
}

/** Days are whole here: half a day of certificate is not a number anyone acts on. */
export function formatCertificateAge(daysRemaining: number): string {
  const days = Math.floor(daysRemaining);
  if (days < 0) return "expired";
  if (days === 0) return "expires today";
  return days === 1 ? "1 day" : `${days} days`;
}

/**
 * The probes as a strip, oldest on the left. The API answers newest first because that
 * is what a list wants; a strip read left to right is time, so it is reversed once here
 * rather than in the component's JSX.
 */
export function runsOldestFirst(runs: readonly CheckRun[]): CheckRun[] {
  return [...runs].reverse();
}

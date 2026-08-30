import type { CheckResultSummary, CheckRun } from "./api";

/**
 * What the readings mean, decided once.
 *
 * Health is the absence of colour, so most of what this module returns is "none". It
 * exists to keep the three questions the screen answers — is it up, did it stay up, is
 * the certificate about to end it — from being three inline ternaries per row.
 */

/** Ordered worst first; a row wears the worst thing true of it. */
export type Urgency = "critical" | "warning" | "none";

export type LiveState = "up" | "down" | "unknown";

/**
 * Let's Encrypt begins renewing at 30 days remaining and issues 90-day certificates, so
 * 30 days left is routine and colouring it would make the screen cry wolf once a month.
 * Under 21 days means a renewal window has already been missed; under 7 means the
 * outage is close enough to be someone's afternoon.
 */
const CERTIFICATE_WARNING_DAYS = 21;
const CERTIFICATE_CRITICAL_DAYS = 7;

const FULL_UPTIME_PERCENT = 100;

export function liveStateOf(check: CheckResultSummary): LiveState {
  if (check.runs === 0 || check.up === null) return "unknown";
  return check.up ? "up" : "down";
}

/**
 * Down now is critical. Anything that failed and recovered is a warning: the window
 * says it happened, and a check that flaps is a check that will be down again.
 */
export function uptimeUrgency(check: CheckResultSummary): Urgency {
  if (liveStateOf(check) === "down") return "critical";
  if (check.runs > 0 && check.successful_runs < check.runs) return "warning";
  return "none";
}

export function certificateUrgency(daysRemaining: number): Urgency {
  if (daysRemaining <= CERTIFICATE_CRITICAL_DAYS) return "critical";
  if (daysRemaining <= CERTIFICATE_WARNING_DAYS) return "warning";
  return "none";
}

/** The worst thing true of a check, which is what its row is marked with. */
export function checkUrgency(check: CheckResultSummary): Urgency {
  const certificate = check.tls ? certificateUrgency(check.tls.days_remaining) : "none";
  return worst(uptimeUrgency(check), certificate);
}

export function worst(left: Urgency, right: Urgency): Urgency {
  if (left === "critical" || right === "critical") return "critical";
  if (left === "warning" || right === "warning") return "warning";
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

export function failedRunCount(check: CheckResultSummary): number {
  return Math.max(0, check.runs - check.successful_runs);
}

export function isPerfect(check: CheckResultSummary): boolean {
  return check.runs > 0 && check.uptime_percent >= FULL_UPTIME_PERCENT;
}

/**
 * The probes as a strip, oldest on the left. The API answers newest first because that
 * is what a list wants; a strip read left to right is time, so it is reversed once here
 * rather than in the component's JSX.
 */
export function runsOldestFirst(runs: readonly CheckRun[]): CheckRun[] {
  return [...runs].reverse();
}

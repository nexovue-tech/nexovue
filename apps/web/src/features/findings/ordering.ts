import type { Finding, FindingSeverity, FindingState } from "./api";

/**
 * The order the feed is read in, which F1 AC1.5 fixes: **active before resolved, then
 * severity, then recency.**
 *
 * Pure and separate from the screen because it is the part that can be wrong without
 * looking wrong. A feed sorted by recency alone buries a critical finding from an hour
 * ago under a warning from a minute ago, and the page still renders perfectly.
 */

/** Lower sorts first. Acknowledged is still active — somebody saw it, nobody fixed it. */
const BY_STATE: Record<FindingState, number> = {
  active: 0,
  acknowledged: 1,
  resolved: 2,
};

const BY_SEVERITY: Record<FindingSeverity, number> = {
  critical: 0,
  warning: 1,
};

/**
 * Newest first within a tier.
 *
 * A resolved finding is ordered by when it *resolved*, not when it started: the question
 * a reader has about the quiet part of the feed is "what stopped being wrong, and when",
 * and ordering those by start date would interleave them by something nobody is reading.
 */
function instantOf(finding: Finding): number {
  const when =
    finding.state === "resolved" ? (finding.resolved_at ?? finding.started_at) : finding.started_at;
  const parsed = Date.parse(when);
  // An unparseable timestamp sorts last rather than throwing the whole feed into
  // NaN comparisons, where the order becomes whatever the sort happened to do.
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

export function orderFindings(findings: readonly Finding[]): Finding[] {
  return [...findings].sort((left, right) => {
    const byState = BY_STATE[left.state] - BY_STATE[right.state];
    if (byState !== 0) return byState;

    const bySeverity = BY_SEVERITY[left.severity] - BY_SEVERITY[right.severity];
    if (bySeverity !== 0) return bySeverity;

    return instantOf(right) - instantOf(left);
  });
}

/** Whether anything still needs somebody. Resolved findings do not count. */
export function needsAttention(findings: readonly Finding[]): boolean {
  return findings.some((finding) => finding.state !== "resolved");
}

import type { Finding, FindingSeverity } from "./api";

/**
 * The order the feed is read in (F1 AC1.5, F22 FR21): **open and unacknowledged, or a
 * case whose finding came back and is open; then acknowledged; then closed with an
 * active case; then the rest closed. Within a tier, the case's severity, then
 * recency.**
 *
 * The server ranks by the same rule (`findings/queries.py`, `CASE_RANK`), and
 * `feed-order.fixture.json` holds the two to one test set (AC27), because a feed
 * the two sort differently reorders under the reader's eyes on every refresh.
 *
 * Pure and separate from the screen because it is the part that can be wrong without
 * looking wrong. A feed sorted by recency alone buries a critical finding from an hour
 * ago under a warning from a minute ago, and the page still renders perfectly.
 */

/** Lower sorts first. Acknowledged is still open — somebody saw it, nobody fixed it. */
function tierOf(finding: Finding): number {
  if (finding.state === "open") return 0;
  // A recovered root whose joined finding is open: never "recovered" (AC59).
  if (
    finding.state === "closed" &&
    finding.case_state === "investigating" &&
    finding.case_signal === "open"
  ) {
    return 0;
  }
  if (finding.state === "acknowledged") return 1;
  if (finding.case_state === "investigating") return 2;
  return 3;
}

const BY_SEVERITY: Record<FindingSeverity, number> = {
  critical: 0,
  warning: 1,
};

/**
 * Newest first within a tier.
 *
 * A closed finding is ordered by when it *closed*, not when it started: the question
 * a reader has about the quiet part of the feed is "what stopped being wrong, and when",
 * and ordering those by start date would interleave them by something nobody is reading.
 */
function instantOf(finding: Finding): number {
  const when =
    finding.state === "closed" ? (finding.closed_at ?? finding.started_at) : finding.started_at;
  const parsed = Date.parse(when);
  // An unparseable timestamp sorts last rather than throwing the whole feed into
  // NaN comparisons, where the order becomes whatever the sort happened to do.
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

export function orderFindings(findings: readonly Finding[]): Finding[] {
  return [...findings].sort((left, right) => {
    const byTier = tierOf(left) - tierOf(right);
    if (byTier !== 0) return byTier;

    // The worse of the row's and an open joined finding's (FR21).
    const bySeverity = BY_SEVERITY[left.case_severity] - BY_SEVERITY[right.case_severity];
    if (bySeverity !== 0) return bySeverity;

    return instantOf(right) - instantOf(left);
  });
}

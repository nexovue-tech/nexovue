import type { ClosedReason } from "./api";

/**
 * How a closed finding closed, in the reader's words. Shared by the feed's card and
 * the finding's own page, so the two never describe one close differently.
 *
 * Never "Resolved": a finding that closed because the service went quiet, or because
 * nobody answered for a week, was not fixed, and saying so would be a claim we cannot
 * support.
 */
export const CLOSED_AS: Record<ClosedReason, string> = {
  recovered: "Recovered",
  false_alarm: "Closed as a false alarm",
  new_normal: "Accepted as the new normal",
  adopted: "Accepted as the new normal after 7 days with no answer",
  service_gone: "Service stopped sending",
  could_not_be_judged: "Closed: too quiet to judge for 6 hours",
};

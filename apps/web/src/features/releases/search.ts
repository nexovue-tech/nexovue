/**
 * `/releases/$releaseId?period=&finding=&finding_at=`: which period the page reads (the
 * latest when absent), and the finding it was opened from, whose start the comparison's
 * window reaches (FR22) and to which it links back.
 */
export interface ReleaseSearch {
  period?: string;
  finding?: string;
  finding_at?: string;
}

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value !== "" ? value : undefined;

export function parseReleaseSearch(search: Record<string, unknown>): ReleaseSearch {
  const parsed: ReleaseSearch = {};
  const period = text(search.period);
  if (period !== undefined) parsed.period = period;
  const finding = text(search.finding);
  const findingAt = text(search.finding_at);
  // Both or neither: a finding with no readable start cannot place the window.
  if (finding !== undefined && findingAt !== undefined && Number.isFinite(Date.parse(findingAt))) {
    parsed.finding = finding;
    parsed.finding_at = findingAt;
  }
  return parsed;
}

/**
 * `/releases/$releaseId?period=&finding=`: which period the page reads (the latest when
 * absent), and the finding it was opened from, whose start the comparison's window
 * reaches (FR22; the API chooses it) and to which it links back.
 */
export interface ReleaseSearch {
  period?: string;
  finding?: string;
}

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value !== "" ? value : undefined;

export function parseReleaseSearch(search: Record<string, unknown>): ReleaseSearch {
  const parsed: ReleaseSearch = {};
  const period = text(search.period);
  if (period !== undefined) parsed.period = period;
  const finding = text(search.finding);
  if (finding !== undefined) parsed.finding = finding;
  return parsed;
}

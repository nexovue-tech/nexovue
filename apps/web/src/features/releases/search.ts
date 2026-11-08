/** `/releases/$releaseId?period=`: which period the page reads; the latest when absent. */
export interface ReleaseSearch {
  period?: string;
}

export function parseReleaseSearch(search: Record<string, unknown>): ReleaseSearch {
  return typeof search.period === "string" && search.period !== "" ? { period: search.period } : {};
}

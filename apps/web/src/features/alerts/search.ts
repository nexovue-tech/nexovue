// What a route reads from its URL, apart from the screen it renders, so the router
// can import it without pulling the screen into the first download (group 9, D3).

export interface NewRuleSearch {
  /** Prefilled from a check's or a service's page — "Alert me about this" (FR24). */
  check?: string;
  service?: string;
}

export function parseNewRuleSearch(search: Record<string, unknown>): NewRuleSearch {
  if (typeof search.check === "string" && search.check) return { check: search.check };
  if (typeof search.service === "string" && search.service) return { service: search.service };
  return {};
}

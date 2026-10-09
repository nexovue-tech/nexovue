/**
 * What narrows the call list.
 *
 * **Both go to the server, not the browser.** `service_match`/`model_match`
 * (`llm_observability/queries.py`) are substring matches over values this
 * screen never enumerates in advance — unlike `metrics/filters.ts`'s `shape`,
 * there is no closed set of services or model names to offer in a `FilterBar`
 * select. The typed-search pattern `metrics/MetricsScreen.tsx` already built
 * for its own free-text name field is the one precedent for exactly this case,
 * so this screen uses two of those rather than a `FilterBar` neither field fits.
 */

import type { FilterValues } from "@/shared/components/FilterBar";

/** The API's own parameter names, so the URL and the request spell them once. */
export const SERVICE_KEY = "service";
export const MODEL_KEY = "model";

/** Every filter the URL holds, trimmed; a blank value is the same as none. */
export function llmCallFilters(search: Record<string, unknown>): FilterValues {
  const values: Record<string, string> = {};
  const service = text(search[SERVICE_KEY]);
  const model = text(search[MODEL_KEY]);
  if (service !== undefined) values[SERVICE_KEY] = service;
  if (model !== undefined) values[MODEL_KEY] = model;
  return values;
}

export function llmCallsNarrowed(values: FilterValues): boolean {
  return Boolean(values[SERVICE_KEY]) || Boolean(values[MODEL_KEY]);
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

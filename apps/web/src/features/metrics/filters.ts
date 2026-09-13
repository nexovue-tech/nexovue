/**
 * What narrows the catalogue.
 *
 * **All three go to the server, and the cap is the reason.** The catalogue is
 * read one past `MAX_METRICS = 500` and the screen already renders the cut —
 * "this project reports more metric names than this list holds". A filter
 * applied in the browser would narrow that prefix, so "only gauges" would mean
 * "the gauges among the busiest five hundred names" while the sentence under the
 * list went on blaming traffic for what the filter hid. Five hundred is not a
 * theoretical ceiling: the development stack reports 320 distinct names over six
 * hours from one demo application, and a customer with a few runtimes and its own
 * business metrics passes it.
 *
 * The name is not a `FilterDefinition`: `FilterBar` offers a select or a toggle,
 * and three hundred names are typed at rather than picked from. It still lives
 * in the URL, like the other two, so a narrowed catalogue is a link.
 */

import type { FilterDefinition, FilterValues } from "@/shared/components/FilterBar";
import { appliedFilters } from "@/shared/components/FilterBar";
import { parseFilterValues } from "@/shared/filters";

/** The API's own parameter name, so the URL and the request spell it once. */
export const METRIC_NAME_KEY = "name";

export const METRIC_FILTERS: readonly FilterDefinition[] = [
  {
    key: "shape",
    label: "Shape",
    // The five OTLP point shapes, spelled as `metrics/queries.py` reports them —
    // a value this list invents comes back as an empty catalogue that reads as
    // "this project sends no gauges".
    control: {
      kind: "select",
      options: [
        { value: "gauge", label: "Gauge" },
        { value: "sum", label: "Sum" },
        { value: "histogram", label: "Histogram" },
        { value: "exponential histogram", label: "Exponential histogram" },
        { value: "summary", label: "Summary" },
      ],
    },
  },
  {
    key: "readable_only",
    label: "Only charted",
    control: { kind: "toggle", on: "true" },
  },
];

/** Every filter the URL holds: the two declared controls plus the typed name. */
export function metricFilters(search: Record<string, unknown>): FilterValues {
  const typed = typeof search[METRIC_NAME_KEY] === "string" ? search[METRIC_NAME_KEY].trim() : "";
  return {
    ...parseFilterValues(METRIC_FILTERS, search),
    ...(typed === "" ? {} : { [METRIC_NAME_KEY]: typed }),
  };
}

/**
 * Whether anything is narrowing the list, which decides which empty state the
 * screen is showing. `appliedFilters` alone is not the answer here — it only
 * knows about the declared controls, and a typed name is a filter too.
 */
export function metricsNarrowed(values: FilterValues): boolean {
  return appliedFilters(METRIC_FILTERS, values).length > 0 || Boolean(values[METRIC_NAME_KEY]);
}

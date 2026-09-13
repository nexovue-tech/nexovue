/**
 * What narrows the dependency graph.
 *
 * A map with three hundred edges is not a map. Both filters go to the server:
 * the graph is capped at a thousand edges ranked busiest-first, so "only failing
 * edges" applied afterwards would answer "the failing ones among the busiest
 * thousand" — and a failing edge is often a quiet one.
 *
 * The volume floor is asked in calls per minute because that is what an edge is
 * labelled with. The stored column is a count, and the API multiplies by the
 * window it is reading rather than asking a person to think in "calls per
 * window".
 */

import type { FilterDefinition, FilterOption } from "@/shared/components/FilterBar";

/** Round numbers, not a continuous field to mistype into. */
const VOLUME_FLOORS: readonly FilterOption[] = [
  { value: "1", label: "1 / min" },
  { value: "10", label: "10 / min" },
  { value: "100", label: "100 / min" },
];

export const SERVICE_MAP_FILTERS: readonly FilterDefinition[] = [
  {
    key: "min_calls_per_minute",
    label: "Busier than",
    control: { kind: "select", options: VOLUME_FLOORS },
    describe: (value) =>
      VOLUME_FLOORS.find((floor) => floor.value === value)?.label ?? `${value} / min`,
  },
  {
    key: "failing_only",
    label: "Only failing",
    control: { kind: "toggle", on: "true" },
  },
];

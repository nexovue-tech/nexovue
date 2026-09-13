/**
 * What narrows the error list.
 *
 * "Only new" is the server's to answer, and not only because the list is capped
 * at fifty. A new incident is usually not yet noisy, so it sorts behind every
 * long-running bug — exactly the row this filter is asked to find, and exactly
 * the row a filter over the page cannot reach.
 */

import type { FilterDefinition } from "@/shared/components/FilterBar";

import type { ErrorGroupSummary } from "./api";

/**
 * The filters this screen offers, given the groups it has.
 *
 * The service list is built from the groups in hand. A service named by a link
 * from elsewhere may not be among them — that is the point of arriving scoped —
 * and `FilterBar` keeps a selected value it was not offered, so the control and
 * the results cannot disagree.
 */
export function errorFilters(groups: readonly ErrorGroupSummary[]): FilterDefinition[] {
  return [
    {
      key: "service_name",
      label: "Service",
      control: { kind: "select", options: serviceOptions(groups) },
    },
    {
      key: "new_only",
      label: "Only new",
      control: { kind: "toggle", on: "true" },
    },
  ];
}

/** Every service any group in this window happened in, named once. */
function serviceOptions(groups: readonly ErrorGroupSummary[]) {
  const named = new Set<string>();
  for (const group of groups) for (const service of group.services) named.add(service);
  return [...named].sort().map((service) => ({ value: service, label: service }));
}

/** The shape the route validates against before any group has arrived. */
export const ERROR_FILTERS: readonly FilterDefinition[] = errorFilters([]);

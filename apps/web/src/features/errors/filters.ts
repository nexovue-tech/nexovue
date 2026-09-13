/**
 * What narrows the error list.
 *
 * "Only new" is the server's to answer, and not only because the list is capped
 * at fifty. A new incident is usually not yet noisy, so it sorts behind every
 * long-running bug — exactly the row this filter is asked to find, and exactly
 * the row a filter over the page cannot reach.
 */

import type { FilterDefinition } from "@/shared/components/FilterBar";

export const ERROR_FILTERS: readonly FilterDefinition[] = [
  {
    key: "new_only",
    label: "Only new",
    control: { kind: "toggle", on: "true" },
  },
];

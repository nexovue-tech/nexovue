/**
 * What narrows the service list, and how it is ranked.
 *
 * Both go to the server. `order` looks like a presentation choice and is not:
 * the API returns the first hundred rows of whichever order it was asked for,
 * so re-sorting them here would answer "the slowest among the hundred with the
 * most errors". A service that is the slowest in the project and fails for
 * nobody is exactly the row that cannot reach.
 */

import type { FilterDefinition } from "@/shared/components/FilterBar";

export const SERVICE_FILTERS: readonly FilterDefinition[] = [
  {
    key: "span_kind",
    label: "Traffic",
    control: {
      kind: "select",
      options: [
        { value: "SERVER", label: "HTTP" },
        { value: "CONSUMER", label: "Queues" },
      ],
    },
  },
  {
    key: "with_errors",
    label: "Only failing",
    control: { kind: "toggle", on: "true" },
  },
];

/**
 * `worst` is the API's own order — errors, then the slow tail, then volume — and
 * it is the product. `slowest` is the escape hatch for the window where nothing
 * is failing and something is still dragging.
 */
export const SERVICE_ORDERS = ["worst", "slowest"] as const;
export type ServiceOrder = (typeof SERVICE_ORDERS)[number];

export const ORDER_OPTIONS: ReadonlyArray<{ id: ServiceOrder; label: string }> = [
  { id: "worst", label: "worst" },
  { id: "slowest", label: "slowest" },
];

/** Anything the URL does not recognise is the ranking the screen opens with. */
export function parseServiceOrder(value: unknown): ServiceOrder {
  return SERVICE_ORDERS.find((candidate) => candidate === value) ?? "worst";
}

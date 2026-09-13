/**
 * What narrows the slow-statement list.
 *
 * Both go to the server. The list is capped at five hundred statements, so
 * "slower than 250ms" asked of what came back would answer "the slow ones among
 * the five hundred worst" — which is usually the same set, right up until it
 * silently is not.
 *
 * The floor is tested against the *worst* reading rather than the latest,
 * because that is what the list is ranked by: a statement that spiked to two
 * seconds and has since settled is the one worth finding.
 */

import type { FilterDefinition, FilterOption } from "@/shared/components/FilterBar";

import type { StatementCost } from "./api";

/** Round numbers a person reaches for, not a continuous field to mistype into. */
const MEAN_FLOORS: readonly FilterOption[] = [
  { value: "10", label: "10 ms" },
  { value: "50", label: "50 ms" },
  { value: "250", label: "250 ms" },
  { value: "1000", label: "1 s" },
];

export function databaseFilters(statements: readonly StatementCost[]): FilterDefinition[] {
  return [
    {
      key: "server",
      label: "Server",
      control: { kind: "select", options: serverOptions(statements) },
    },
    {
      key: "min_mean_ms",
      label: "Slower than",
      control: { kind: "select", options: MEAN_FLOORS },
      describe: (value) =>
        MEAN_FLOORS.find((floor) => floor.value === value)?.label ?? `${value} ms`,
    },
  ];
}

function serverOptions(statements: readonly StatementCost[]): FilterOption[] {
  const named = [...new Set(statements.map((row) => row.server.address).filter(Boolean))].sort();
  return named.map((server) => ({ value: server, label: server }));
}

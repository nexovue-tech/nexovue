/**
 * What narrows the check list.
 *
 * Both are answered from the definitions, which hold every check a project has (at
 * most a hundred) and the state each one's runs confirmed. "Only down" therefore means
 * *confirmed* down, the same word the row shows, rather than "the last probe failed".
 */

import type { FilterDefinition } from "@/shared/components/FilterBar";

import type { CheckDefinition } from "./api";
import { CHECK_TYPES } from "./check-model";
import { KIND_DESCRIPTIONS } from "./NewCheckScreen";

export const SYNTHETIC_FILTERS: readonly FilterDefinition[] = [
  {
    key: "check_type",
    label: "Kind",
    control: {
      kind: "select",
      options: CHECK_TYPES.map((value) => ({ value, label: KIND_DESCRIPTIONS[value].title })),
    },
  },
  {
    key: "down_only",
    label: "Only down",
    control: { kind: "toggle", on: "true" },
  },
];

export function visibleChecks(
  checks: readonly CheckDefinition[],
  filters: Readonly<Record<string, string | undefined>>,
): CheckDefinition[] {
  return checks.filter(
    (check) =>
      (!filters.check_type || check.type === filters.check_type) &&
      (filters.down_only !== "true" || check.displayed_state === "down"),
  );
}

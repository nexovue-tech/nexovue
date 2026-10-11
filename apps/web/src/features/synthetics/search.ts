// What a route reads from its URL, apart from the screen it renders, so the router
// can import it without pulling the screen into the first download (group 9, D3).

import type { CheckType } from "./check-model";
import { CHECK_TYPES } from "./check-model";

export type CheckTab = "overview" | "edit";

export function parseCheckSearch(search: Record<string, unknown>): {
  tab?: CheckTab;
  run?: string;
} {
  return {
    ...(search.tab === "edit" ? { tab: "edit" as const } : {}),
    ...(typeof search.run === "string" && search.run ? { run: search.run } : {}),
  };
}

export function parseNewCheckSearch(search: Record<string, unknown>): { kind?: CheckType } {
  const kind = search.kind;
  return typeof kind === "string" && (CHECK_TYPES as readonly string[]).includes(kind)
    ? { kind: kind as CheckType }
    : {};
}

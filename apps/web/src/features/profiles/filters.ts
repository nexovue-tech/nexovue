import type { FilterDefinition, FilterOption } from "@/shared/components/FilterBar";

import type { FilterValue, ProfileFilters } from "./api";

/**
 * The host, version and environment filters, built from the values the profile
 * actually carries (FR8) — a menu offering a value with no samples behind it would
 * answer every choice with an empty graph.
 */

/**
 * A sample that named no host (or version, or environment) is a real subset, and
 * `FilterBar` reads an empty value as "no filter" — so the empty value travels
 * under this name in the URL and the menus, and as "" to the API.
 */
export const NOT_STATED = "(not stated)";

export const PROFILE_FILTER_KEYS = ["host", "version", "env"] as const;
export type ProfileFilterKey = (typeof PROFILE_FILTER_KEYS)[number];

export function profileFilterDefinitions(filters: ProfileFilters | undefined): FilterDefinition[] {
  return [
    {
      key: "host",
      label: "Host",
      control: { kind: "select", options: options(filters?.hosts) },
      describe: (value) => value,
    },
    {
      key: "version",
      label: "Version",
      control: { kind: "select", options: options(filters?.versions) },
      describe: (value) => value,
    },
    {
      key: "env",
      label: "Environment",
      control: { kind: "select", options: options(filters?.environments) },
      describe: (value) => value,
    },
  ];
}

/** What the API is asked for: the chosen value, with "not stated" as the empty string. */
export function apiValue(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  return value === NOT_STATED ? "" : value;
}

function options(values: readonly FilterValue[] | undefined): FilterOption[] {
  return (values ?? []).map(({ value, samples }) => {
    const shown = value === "" ? NOT_STATED : value;
    return { value: shown, label: `${shown} (${samples.toLocaleString("en")})` };
  });
}

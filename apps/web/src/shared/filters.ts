/**
 * Filter values, held in the URL.
 *
 * The URL is the state, not a copy of it. Someone who has narrowed a screen to
 * the one failing container has done real work, and the way they hand that to a
 * colleague is the address bar — so every filter round-trips through the query
 * string and nothing lives only in component state.
 *
 * Absent means "not filtering". A blank string is the same as no key at all and
 * is dropped rather than sent, because `?kind=` reads as a filter and is not one.
 *
 * The screen's own `FilterDefinition` list drives both directions, so a screen
 * declares its filters once and gets the URL handling from that.
 */

import type { FilterDefinition, FilterValues } from "@/shared/components/FilterBar";

/**
 * Read the URL's own search object into values.
 *
 * A value no longer offered is kept rather than dropped: a shared link can name
 * a host that has since gone quiet, and `FilterBar` shows it so the control and
 * the results agree. A toggle is the exception — it has exactly one value that
 * means "on", and anything else in the URL is not that filter.
 */
export function parseFilterValues(
  definitions: readonly FilterDefinition[],
  search: Record<string, unknown>,
): FilterValues {
  const values: Record<string, string> = {};
  for (const definition of definitions) {
    const raw = search[definition.key];
    const text = typeof raw === "string" ? raw.trim() : "";
    if (text === "") continue;
    if (definition.control.kind === "toggle") {
      if (text === definition.control.on) values[definition.key] = text;
      continue;
    }
    values[definition.key] = text;
  }
  return values;
}

/** The query string these values travel as. Keys match the API's own names. */
export function toFilterSearch(values: FilterValues): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values).filter(
      (entry): entry is [string, string] => entry[1] !== undefined && entry[1] !== "",
    ),
  );
}

/** One filter changed, with an absent value removing its key entirely. */
export function withFilter(
  values: FilterValues,
  key: string,
  value: string | undefined,
): FilterValues {
  const next = { ...values, [key]: value };
  return toFilterSearch(next);
}

/** What the request sends: the same values, with no key for what is not applied. */
export function filterParams(values: FilterValues): Record<string, string> {
  return toFilterSearch(values);
}

/**
 * A stable key for caching, so two different filter sets are two different
 * queries and the same set in a different order is one.
 */
export function filterCacheKey(values: FilterValues): string {
  return Object.entries(toFilterSearch(values))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join("&");
}

// What a route reads from its URL, apart from the screen it renders, so the router
// can import it without pulling the screen into the first download (group 9, D3).

import type { BaselineRange, CompareBy } from "./CompareView";

/** What the URL carries, so a profile someone found can be shared. */
export interface ProfilesSearch {
  service?: string;
  type?: string;
  host?: string;
  version?: string;
  env?: string;
  /** A span's own samples only (FR10). */
  trace?: string;
  span?: string;
  /** Compare mode (FR9). */
  compare?: CompareBy;
  base?: string;
  against?: string;
  baseline?: BaselineRange;
}

const HEX_TRACE = /^[0-9a-f]{32}$/;
const HEX_SPAN = /^[0-9a-f]{16}$/;

export function parseProfilesSearch(search: Record<string, unknown>): ProfilesSearch {
  const named = (value: unknown) => (typeof value === "string" && value !== "" ? value : undefined);
  const matching = (value: unknown, pattern: RegExp) => {
    const text = named(value);
    return text !== undefined && pattern.test(text) ? text : undefined;
  };
  const oneOf = <T extends string>(value: unknown, allowed: readonly T[]) =>
    allowed.find((candidate) => candidate === value);
  const trace = matching(search.trace, HEX_TRACE);
  const span = matching(search.span, HEX_SPAN);
  return {
    service: named(search.service),
    type: named(search.type),
    host: named(search.host),
    version: named(search.version),
    env: named(search.env),
    // A span is named by both, or it is not named.
    ...(trace && span ? { trace, span } : {}),
    compare: oneOf(search.compare, ["versions", "ranges"] as const),
    base: named(search.base),
    against: named(search.against),
    baseline: oneOf(search.baseline, ["previous", "day", "week"] as const),
  };
}

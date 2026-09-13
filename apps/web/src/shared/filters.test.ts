import { describe, expect, it } from "vitest";

import type { FilterDefinition } from "./components/FilterBar";
import { filterCacheKey, parseFilterValues, toFilterSearch, withFilter } from "./filters";

const DEFINITIONS: readonly FilterDefinition[] = [
  {
    key: "kind",
    label: "Kind",
    control: { kind: "select", options: [{ value: "host", label: "Hosts" }] },
  },
  { key: "stale_only", label: "Only stale", control: { kind: "toggle", on: "true" } },
];

describe("parseFilterValues", () => {
  it("ignores a key the screen does not declare", () => {
    // A hand-edited or stale link should show what it can, not a 422 the reader
    // cannot act on.
    expect(parseFilterValues(DEFINITIONS, { nonsense: "x" })).toEqual({});
  });

  it("treats a blank value as not filtering", () => {
    expect(parseFilterValues(DEFINITIONS, { kind: "   " })).toEqual({});
  });

  it("keeps a value the screen no longer offers", () => {
    // A shared link naming a host that has since gone quiet. Dropping it would
    // show the full list under a control that still said it was narrowed.
    expect(parseFilterValues(DEFINITIONS, { kind: "web-07" })).toEqual({ kind: "web-07" });
  });

  it("accepts only the one value a toggle means by on", () => {
    expect(parseFilterValues(DEFINITIONS, { stale_only: "true" })).toEqual({ stale_only: "true" });
    expect(parseFilterValues(DEFINITIONS, { stale_only: "yes" })).toEqual({});
  });

  it("ignores a value that is not a string", () => {
    expect(parseFilterValues(DEFINITIONS, { kind: 7 })).toEqual({});
  });
});

describe("toFilterSearch", () => {
  it("drops absent and blank keys rather than sending them", () => {
    expect(toFilterSearch({ kind: "host", stale_only: undefined, host: "" })).toEqual({
      kind: "host",
    });
  });
});

describe("withFilter", () => {
  it("removes the key when the value is absent", () => {
    expect(withFilter({ kind: "host", stale_only: "true" }, "kind", undefined)).toEqual({
      stale_only: "true",
    });
  });

  it("replaces the value when one is given", () => {
    expect(withFilter({ kind: "host" }, "kind", "container")).toEqual({ kind: "container" });
  });
});

describe("filterCacheKey", () => {
  it("is the same for the same filters written in a different order", () => {
    // Otherwise the cache holds two entries for one answer, and the screen
    // refetches whenever a filter is removed and re-added.
    expect(filterCacheKey({ kind: "host", stale_only: "true" })).toBe(
      filterCacheKey({ stale_only: "true", kind: "host" }),
    );
  });

  it("is different for different filters", () => {
    expect(filterCacheKey({ kind: "host" })).not.toBe(filterCacheKey({ kind: "container" }));
  });

  it("is empty when nothing is applied", () => {
    expect(filterCacheKey({})).toBe("");
  });
});

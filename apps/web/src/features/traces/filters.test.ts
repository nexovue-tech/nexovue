import { describe, expect, it } from "vitest";

import type { TraceFilters } from "./filters";
import {
  activeFilters,
  clearFilter,
  explainEmptyResult,
  hasAnyFilter,
  parseTraceFilters,
  toSearchParams,
} from "./filters";

const NOTHING_KNOWN = { services: [], operations: [] };
const KNOWN = { services: ["storefront", "payments"], operations: ["GET /checkout"] };

describe("parseTraceFilters", () => {
  it("reads every filter the API accepts out of the URL", () => {
    expect(
      parseTraceFilters({
        service: "storefront",
        operation: "GET /checkout",
        status: "ERROR",
        min_duration_ms: "500",
        max_duration_ms: "2000",
        attr: "db.system",
        attr_value: "postgresql",
      }),
    ).toEqual({
      service: "storefront",
      operation: "GET /checkout",
      status: "ERROR",
      minDurationMs: 500,
      maxDurationMs: 2000,
      attr: "db.system",
      attrValue: "postgresql",
    });
  });

  it("survives a link somebody edited by hand rather than refusing to render", () => {
    expect(
      parseTraceFilters({
        service: "   ",
        status: "MAYBE",
        min_duration_ms: "soon",
        max_duration_ms: "-5",
        unknown: "ignored",
      }),
    ).toEqual({});
  });

  it("accepts a status in either case, because a URL gets retyped", () => {
    expect(parseTraceFilters({ status: "error" }).status).toBe("ERROR");
  });

  it("drops an attribute name with nothing to compare it against", () => {
    expect(parseTraceFilters({ attr: "db.system" })).toEqual({});
  });

  it("round-trips through the query string it is shared as", () => {
    const filters: TraceFilters = {
      service: "storefront",
      status: "OK",
      minDurationMs: 100,
      attr: "db.system",
      attrValue: "postgresql",
    };

    expect(parseTraceFilters(toSearchParams(filters))).toEqual(filters);
  });

  it("puts nothing in the URL for a filter nobody set", () => {
    expect(toSearchParams({})).toEqual({});
    expect(hasAnyFilter({})).toBe(false);
    expect(hasAnyFilter({ service: "storefront" })).toBe(true);
  });

  it("keeps a duration floor of zero, which is a filter and not an absence", () => {
    expect(parseTraceFilters({ min_duration_ms: "0" }).minDurationMs).toBe(0);
    expect(toSearchParams({ minDurationMs: 0 })).toEqual({ min_duration_ms: 0 });
  });
});

describe("clearFilter", () => {
  it("removes one filter and leaves the others in place", () => {
    expect(clearFilter({ service: "storefront", status: "ERROR" }, "status")).toEqual({
      service: "storefront",
    });
  });

  it("removes both halves of the attribute filter, which is one filter", () => {
    expect(clearFilter({ attr: "db.system", attrValue: "postgresql" }, "attr")).toEqual({});
  });
});

describe("activeFilters", () => {
  it("names every filter so each one can be shown and removed", () => {
    expect(
      activeFilters({ service: "storefront", minDurationMs: 500, status: "ERROR" }).map(
        (filter) => filter.key,
      ),
    ).toEqual(["service", "status", "minDurationMs"]);
  });

  it("says what a status means rather than repeating the enum", () => {
    expect(activeFilters({ status: "OK" })[0].value).toBe("no failures");
  });
});

describe("explainEmptyResult", () => {
  it("says the window is empty when nothing was sent at all", () => {
    expect(explainEmptyResult({}, "hour", NOTHING_KNOWN)).toContain("Nothing has been assembled");
  });

  it("names the filter that cannot match, because that is the usual cause", () => {
    const said = explainEmptyResult({ service: "checkout" }, "hour", KNOWN);

    expect(said).toContain("checkout");
    expect(said).toContain("2 services are reporting");
  });

  it("counts one reporting service as one, not as two", () => {
    expect(
      explainEmptyResult({ service: "checkout" }, "hour", {
        services: ["storefront"],
        operations: [],
      }),
    ).toContain("1 service is reporting");
  });

  it("lists the filters when each one names something that does exist", () => {
    const said = explainEmptyResult(
      { service: "storefront", minDurationMs: 500 },
      "6 hours",
      KNOWN,
    );

    expect(said).toContain("service storefront");
    expect(said).toContain("slower than 500 ms");
    expect(said).toContain("Drop a filter");
  });

  it("does not blame a filter when there is none to blame", () => {
    expect(explainEmptyResult({}, "hour", KNOWN)).toContain("Widen the time range");
  });
});

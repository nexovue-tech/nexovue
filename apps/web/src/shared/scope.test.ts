import { describe, expect, it } from "vitest";

import { SCOPED_PATHS, scopedSearch } from "./scope";

describe("carrying a service to the next screen", () => {
  it("uses each destination's own spelling of the parameter", () => {
    // Traces and logs call it `service`; error groups call it `service_name`.
    // The disagreement is the API's, and a screen linking to another should not
    // have to know the other's spelling.
    expect(scopedSearch("traces", "checkout")).toEqual({ service: "checkout" });
    expect(scopedSearch("logs", "checkout")).toEqual({ service: "checkout" });
    expect(scopedSearch("errors", "checkout")).toEqual({ service_name: "checkout" });
  });

  it("carries nothing when there is no service to carry", () => {
    // `?service=` reads as a filter and is not one — it would put the
    // destination into its filtered-empty state for a filter nobody applied.
    expect(scopedSearch("traces", undefined)).toEqual({});
    expect(scopedSearch("traces", "")).toEqual({});
    expect(scopedSearch("traces", "   ")).toEqual({});
  });

  it("trims a name rather than sending the spaces around it", () => {
    expect(scopedSearch("traces", " checkout ")).toEqual({ service: "checkout" });
  });

  it("names a path for every destination it can scope", () => {
    // A destination with a parameter and no path is a link that cannot be built.
    for (const destination of ["traces", "logs", "errors"] as const) {
      expect(SCOPED_PATHS[destination]).toMatch(/^\//);
      expect(Object.keys(scopedSearch(destination, "x"))).toHaveLength(1);
    }
  });
});

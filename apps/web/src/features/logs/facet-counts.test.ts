import { describe, expect, it } from "vitest";

import type { ServiceFacet, SeverityFacet } from "./api";
import { countedSeverity, serviceOptions } from "./LogsScreen";
import { SEVERITY_FILTERS, severityFilterById } from "./severity";

/**
 * A facet is not a list of options — it is what the window actually holds,
 * counted with no filters applied. Without the count a reader picks a service
 * and discovers after two clicks that it has three records; with it they pick
 * the one that has four thousand.
 */

const FACETS: readonly SeverityFacet[] = [
  { severity: "DEBUG", severity_number: 5, records: 900 },
  { severity: "INFO", severity_number: 9, records: 400 },
  { severity: "WARN", severity_number: 13, records: 30 },
  { severity: "ERROR", severity_number: 17, records: 7 },
];

const counted = (id: string) => countedSeverity(severityFilterById(id), FACETS);

describe("counting a severity filter", () => {
  it("sums every level at or above the floor, because that is what it returns", () => {
    // Counting only the exact level would say "Warn+ · 30" on a window holding
    // thirty warnings and seven errors — fewer than the filter actually shows.
    expect(counted("warn")).toBe("Warn+ · 37");
    expect(counted("error")).toBe("Error+ · 7");
    expect(counted("info")).toBe("Info+ · 437");
  });

  it("leaves All uncounted, because it is not a narrowing", () => {
    // A total beside it competes with the caption that already says how many
    // records the window holds.
    expect(counted("all")).toBe(severityFilterById("all").label);
  });

  it("shows a bare label rather than a zero when nothing reached that level", () => {
    // "Error+ · 0" reads as a broken counter. The absence is the answer.
    expect(countedSeverity(severityFilterById("error"), [])).toBe("Error+");
  });

  it("covers every filter the screen offers", () => {
    // A filter added without a count would silently read as "nothing here".
    for (const filter of SEVERITY_FILTERS) {
      expect(countedSeverity(filter, FACETS)).toContain(filter.label);
    }
  });
});

describe("the services a reader can pick", () => {
  const facets: readonly ServiceFacet[] = [
    { service_name: "frontend-proxy", records: 888 },
    { service_name: "nexovue-prober", records: 20 },
  ];

  it("shows each service with what the window holds behind it", () => {
    expect(serviceOptions(facets, undefined).map((option) => option.label)).toEqual([
      "All",
      "frontend-proxy · 888",
      "nexovue-prober · 20",
    ]);
  });

  it("keeps a service that sent nothing this hour but is being filtered on", () => {
    // Found in the browser: arriving from `frontend`, which had no logs in the
    // window, left the control reading "All" while the list was narrowed to it.
    // A native select shows its first option when none matches the value, so
    // the control and the results disagreed with nothing on screen to say so.
    const options = serviceOptions(facets, "frontend");

    expect(options.map((option) => option.value)).toEqual([
      "",
      "frontend",
      "frontend-proxy",
      "nexovue-prober",
    ]);
    // No count beside it: there is nothing to count, and "frontend · 0" would
    // read as a broken counter rather than as an empty result.
    expect(options[1].label).toBe("frontend");
  });

  it("does not repeat a service that is both chosen and present", () => {
    expect(serviceOptions(facets, "frontend-proxy").map((option) => option.value)).toEqual([
      "",
      "frontend-proxy",
      "nexovue-prober",
    ]);
  });
});

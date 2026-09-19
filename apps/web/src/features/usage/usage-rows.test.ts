import { describe, expect, it } from "vitest";

import type { ProjectDayUsage } from "./api";
import { buildUsageRows, projectLabel, unsettledDayCount } from "./usage-rows";

const PRODUCTION = "11111111-1111-4111-8111-111111111111";
const STAGING = "22222222-2222-4222-8222-222222222222";

const NAMES = new Map([
  [PRODUCTION, "production"],
  [STAGING, "staging"],
]);

function usage(day: string, projectId: string, overrides: Partial<ProjectDayUsage> = {}) {
  return {
    project_id: projectId,
    day,
    spans: 100,
    logs: 200,
    metric_points: 300,
    profile_samples: 0,
    bytes_ingested: 1_000,
    services: 3,
    settled: true,
    ...overrides,
  } satisfies ProjectDayUsage;
}

describe("buildUsageRows", () => {
  it("puts the newest day first, whatever order the API returned", () => {
    const rows = buildUsageRows(
      [usage("2026-08-10", PRODUCTION), usage("2026-08-12", PRODUCTION)],
      NAMES,
    );

    expect(rows.map((row) => row.usage.day)).toEqual(["2026-08-12", "2026-08-10"]);
  });

  it("keeps one day's projects together and in a stable order", () => {
    const rows = buildUsageRows(
      [usage("2026-08-10", STAGING), usage("2026-08-10", PRODUCTION)],
      NAMES,
    );

    expect(rows.map((row) => row.projectLabel)).toEqual(["production", "staging"]);
  });

  it("keys a row by the project-day it is, so two projects on a day do not collide", () => {
    const rows = buildUsageRows(
      [usage("2026-08-10", STAGING), usage("2026-08-10", PRODUCTION)],
      NAMES,
    );

    expect(new Set(rows.map((row) => row.key)).size).toBe(2);
  });

  it("carries the day's own settled flag through untouched", () => {
    const rows = buildUsageRows([usage("2026-08-10", PRODUCTION, { settled: false })], NAMES);

    expect(rows[0].usage.settled).toBe(false);
  });
});

describe("projectLabel", () => {
  it("uses the project's name", () => {
    expect(projectLabel(PRODUCTION, NAMES)).toBe("production");
  });

  it("names a deleted project by its id rather than dropping its usage", () => {
    // The rows outlive the project. Hiding them would understate the range.
    expect(projectLabel(STAGING, new Map())).toBe("Project 22222222");
  });
});

describe("unsettledDayCount", () => {
  it("counts days, not rows: two projects unfinished on one day is one day", () => {
    const days = [
      usage("2026-08-12", PRODUCTION, { settled: false }),
      usage("2026-08-12", STAGING, { settled: false }),
      usage("2026-08-11", PRODUCTION),
    ];

    expect(unsettledDayCount(days)).toBe(1);
  });

  it("is zero when every day has finished being counted", () => {
    expect(unsettledDayCount([usage("2026-08-11", PRODUCTION)])).toBe(0);
  });
});

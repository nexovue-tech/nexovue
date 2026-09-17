import { describe, expect, it } from "vitest";

import { parseFilterValues } from "@/shared/filters";

import { CHECK_TYPES } from "./check-model";
import { SYNTHETIC_FILTERS, visibleChecks } from "./filters";
import { KIND_DESCRIPTIONS } from "./NewCheckScreen";
import { checkDefinition } from "./render-harness.test-util";

const CHECKS = [
  checkDefinition({ id: "a", type: "api", displayed_state: "down" }),
  checkDefinition({ id: "b", type: "api", displayed_state: "up" }),
  checkDefinition({ id: "c", type: "tcp", displayed_state: "down" }),
  checkDefinition({ id: "d", type: "dns", displayed_state: null }),
];

const ids = (filters: Record<string, string | undefined>) =>
  visibleChecks(CHECKS, filters).map((check) => check.id);

describe("the synthetics filters", () => {
  it("offer every kind a check can be, under the name the list shows it by (AC29)", () => {
    const kind = SYNTHETIC_FILTERS.find((filter) => filter.key === "check_type");
    if (kind?.control.kind !== "select") throw new Error("Kind is not a select");

    expect(kind.control.options).toEqual(
      CHECK_TYPES.map((value) => ({ value, label: KIND_DESCRIPTIONS[value].title })),
    );
  });

  it("leave the list whole when nothing is chosen", () => {
    expect(ids({})).toEqual(["a", "b", "c", "d"]);
  });

  it("narrow by kind, by confirmed down, and by both", () => {
    expect(ids({ check_type: "api" })).toEqual(["a", "b"]);
    expect(ids({ down_only: "true" })).toEqual(["a", "c"]);
    expect(ids({ check_type: "api", down_only: "true" })).toEqual(["a"]);
  });

  it("do not count a check never judged as down", () => {
    expect(ids({ check_type: "dns", down_only: "true" })).toEqual([]);
  });

  it("read Only down from the address only when it says on", () => {
    expect(parseFilterValues(SYNTHETIC_FILTERS, { down_only: "true" })).toEqual({
      down_only: "true",
    });
    expect(parseFilterValues(SYNTHETIC_FILTERS, { down_only: "yes" })).toEqual({});
  });
});

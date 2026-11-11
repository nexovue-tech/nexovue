import { describe, expect, it } from "vitest";
import { serviceTargetsLine } from "./slo-line";

const on = (service: string | null, state: string) => ({ service_name: service, state });

describe("the service page's reliability targets line (F19 design, *Elsewhere*; T5.7)", () => {
  it("counts this service's targets and how many are running out", () => {
    const targets = [
      on("checkout", "running_out"),
      on("checkout", "on_track"),
      on("cart", "running_out"),
      on(null, "running_out"),
    ];

    expect(serviceTargetsLine(targets, "checkout")).toBe("2 targets. 1 running out.");
  });

  it("says one target in the singular, and nothing about running out when none is", () => {
    expect(serviceTargetsLine([on("checkout", "on_track")], "checkout")).toBe("1 target.");
  });

  it("says nothing on a service with no target", () => {
    expect(serviceTargetsLine([on("cart", "on_track")], "checkout")).toBeNull();
  });
});

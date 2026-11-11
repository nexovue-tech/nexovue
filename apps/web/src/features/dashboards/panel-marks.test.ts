import { describe, expect, it } from "vitest";

import type { DashboardContext, PanelResult, StoredPanel } from "./api";
import { ALL_IN_PROJECT, marksFor, NO_DEPLOY_INFORMATION } from "./panel-marks";

/**
 * Which marks each time panel draws (F20, FR18, T5.5), from data, since jsdom has no
 * canvas: a service's chart its service's and the project-wide findings; a top-N chart its
 * listed services'; a project chart all; a node, check or statement chart all, saying so
 * (AC132). A deploy outside the cap still draws (AC32's data half) and a `seen` mark keeps
 * its basis (AC122). Fixed times only: no wall clock is read.
 */

const START = "2026-11-09T10:00:00.000Z";
const END = "2026-11-09T11:00:00.000Z";
const seconds = (iso: string) => Date.parse(iso) / 1000;

const panel = (kind: string, settings: Record<string, unknown> = {}): StoredPanel => ({
  id: `p_${kind}`,
  kind,
  title: null,
  width: 4,
  height: "medium",
  settings,
});

const finding = (id: string, service: string | null, scope: "service" | "project" = "service") => ({
  id,
  scope,
  service_name: service,
  severity: "critical" as const,
  title: `${service ?? "this project"} is slower than normal`,
  started_at: "2026-11-09T10:10:00.000Z",
  closed_at: "2026-11-09T10:20:00.000Z",
});

const mark = (service: string, version: string, basis: "reported" | "seen") => ({
  release_id: `r-${version}`,
  period_id: basis === "seen" ? `pe-${version}` : null,
  deploy_id: basis === "reported" ? `d-${version}` : null,
  service_name: service,
  version,
  at: "2026-11-09T10:30:00.000Z",
  basis,
  state: basis === "reported" ? ("finished" as const) : null,
});

function context(): DashboardContext {
  return {
    project: { id: "p", name: "production", timezone: "UTC", retention: null },
    window_start: START,
    window_end: END,
    detection: {
      state: "judging",
      services_total: 2,
      services_learning: 0,
      services_judged: 2,
      services_delayed: 0,
      services_muted: 0,
      earliest_judging_date: null,
      not_running_since: null,
      delayed_since: null,
      open_findings: 0,
      services_too_many: 0,
      judged_service_limit: 50,
    },
    ever_reported: true,
    last_seen_at: END,
    findings: [
      finding("f-checkout", "checkout"),
      finding("f-cart", "cart"),
      finding("f-all", null, "project"),
    ],
    findings_truncated: false,
    open_findings: { total: 0, by_service: {} },
    markers: [mark("checkout", "v2.1", "reported"), mark("cart", "v9", "seen")],
    markers_truncated: false,
    markers_cap: 500,
    coverage: { checkout: "deploys_and_versions", cart: "none" },
    incidents: [],
    incidents_truncated: false,
  };
}

const ids = (marking: ReturnType<typeof marksFor>) => marking.marks.spans.map((span) => span.id);
const versions = (marking: ReturnType<typeof marksFor>) =>
  marking.marks.markers.map((marker) => `${marker.version}:${marker.source}`);

describe("which marks a time panel draws (FR18)", () => {
  it("draws a service's own marks and the project-wide findings on its chart", () => {
    const marking = marksFor(panel("service_trend", { service: "checkout" }), context());

    expect(ids(marking)).toEqual(["f-checkout", "f-all"]);
    expect(versions(marking)).toEqual(["v2.1:reported"]);
    expect(marking.note).toBeUndefined();
    expect(marking.coverage).toBeUndefined();
  });

  it("names each finding at its span, so the readout can say it (FR32)", () => {
    const [span] = marksFor(panel("endpoint_trend", { service: "checkout" }), context()).marks
      .spans;

    expect(span).toEqual({
      from: seconds("2026-11-09T10:10:00.000Z"),
      to: seconds("2026-11-09T10:20:00.000Z"),
      id: "f-checkout",
      name: "checkout is slower than normal",
    });
  });

  it("draws a top-N chart's marks for the services it lists", () => {
    const reading: PanelResult = {
      state: "ok",
      kind: "top_services_trend",
      read_at: END,
      bucket_seconds: 60,
      data_starts_at: START,
      data_ends_at: END,
      series: [{ label: "error_rate", service: "cart", points: [] }],
    };
    const marking = marksFor(panel("top_services_trend"), context(), reading);

    expect(ids(marking)).toEqual(["f-cart", "f-all"]);
    expect(versions(marking)).toEqual(["v9:seen"]);
  });

  it("draws every mark on a project chart, a seen version keeping its basis (AC122)", () => {
    const marking = marksFor(panel("project_trend"), context());

    expect(ids(marking)).toEqual(["f-checkout", "f-cart", "f-all"]);
    expect(versions(marking)).toEqual(["v2.1:reported", "v9:seen"]);
    expect(marking.note).toBeUndefined();
  });

  it.each(["node_trend", "top_nodes_trend", "check_trend", "statement_trend"])(
    "says a %s draws all the project's findings and releases (AC132)",
    (kind) => {
      const marking = marksFor(panel(kind), context());

      expect(ids(marking)).toEqual(["f-checkout", "f-cart", "f-all"]);
      expect(marking.note).toBe(ALL_IN_PROJECT);
    },
  );

  it("says F28's words for a service whose coverage is none (AC66, AC132)", () => {
    expect(marksFor(panel("service_trend", { service: "cart" }), context()).coverage).toBe(
      NO_DEPLOY_INFORMATION,
    );
  });

  it("draws nothing on a number or a table, which carry no marks (spec, scope 9)", () => {
    for (const kind of ["project_number", "services_table"]) {
      const marking = marksFor(panel(kind), context());
      expect(marking.marks.spans).toEqual([]);
      expect(marking.marks.markers).toEqual([]);
    }
  });

  it("keeps a deploy among more than 50 newer ones, which the old newest-50 read lost (AC32)", () => {
    const busy = context();
    busy.markers = [
      ...Array.from({ length: 60 }, (_, index) => ({
        ...mark("checkout", `v3.${index}`, "reported"),
        at: "2026-11-09T10:50:00.000Z",
      })),
      { ...mark("checkout", "v1.0", "reported"), at: "2026-11-09T10:05:00.000Z" },
    ];

    const marking = marksFor(panel("service_trend", { service: "checkout" }), busy);

    expect(versions(marking)).toContain("v1.0:reported");
  });
});

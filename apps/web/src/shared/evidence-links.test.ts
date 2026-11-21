import { describe, expect, it } from "vitest";
import {
  dependencySearch,
  type ExplanationFactTarget,
  factLink,
  findingWindow,
  lookNext,
  WINDOW_LEAD_MS,
  WINDOW_TRAIL_MS,
} from "./evidence-links";
import fixture from "./evidence-links.fixture.json";
import { formatDate } from "./format/time";

const PROJECT = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0021";
const FINDING = "01a1c18a-3778-726a-ac51-855da271a38a";
const STARTED = "2026-11-09T14:02:00.000Z";
const CLOSED = "2026-11-09T14:30:00.000Z";
const NOW = Date.parse("2026-11-09T15:00:00.000Z");

const source = {
  project_id: PROJECT,
  finding_id: FINDING,
  started_at: STARTED,
  closed_at: CLOSED,
  raw_kept_until: "2026-11-23T00:00:00.000Z",
  service_name: "checkout",
  signals: [],
};
const WINDOW = {
  from: new Date(Date.parse(STARTED) - WINDOW_LEAD_MS).toISOString(),
  to: new Date(Date.parse(CLOSED) + WINDOW_TRAIL_MS).toISOString(),
  project: PROJECT,
  finding: FINDING,
};

describe("a finding's window (design, *Links*)", () => {
  it("runs from 15 minutes before its start to 5 after its end", () => {
    expect(findingWindow(source, NOW)).toEqual({ from: WINDOW.from, to: WINDOW.to });
  });

  it("runs to now while it is open, and never past now", () => {
    expect(findingWindow({ ...source, closed_at: null }, NOW).to).toBe(new Date(NOW).toISOString());
  });
});

describe("Look next (FR42, AC21, AC66)", () => {
  it("opens each screen for this service and the finding's window, with the way back", () => {
    const links = lookNext(source, NOW);

    expect(links.map((link) => link.label)).toEqual([
      "Traces",
      "Logs",
      "Errors",
      "Deploys",
      "Infrastructure (whole project)",
    ]);
    for (const link of links) {
      if (link.kind !== "link") throw new Error("expected links");
      expect(link.search).toMatchObject(WINDOW);
    }
    const [traces, logs, errors] = links;
    expect(traces).toMatchObject({ to: "/traces", search: { service: "checkout" } });
    expect(logs).toMatchObject({ to: "/logs", search: { service: "checkout" } });
    expect(errors).toMatchObject({ to: "/errors", search: { service_name: "checkout" } });
  });

  it("opens the project's data, unnarrowed, for a project finding or an incident", () => {
    const [traces] = lookNext({ ...source, service_name: null, finding_id: undefined }, NOW);

    expect(traces).toMatchObject({ kind: "link", to: "/traces" });
    if (traces.kind !== "link") throw new Error("expected a link");
    expect(traces.search.service).toBeUndefined();
    expect(traces.search.finding).toBeUndefined();
  });

  it("opens a slower finding's slowest traces, at its worst reading", () => {
    const [traces] = lookNext(
      {
        ...source,
        signals: [
          {
            signal: "slower",
            key: "slower",
            normal: 110,
            current: 330,
            worst: 412.7,
            unit: "ms",
            comparison: "",
            endpoint: null,
          } as never,
        ],
      },
      NOW,
    );

    expect(traces).toMatchObject({ search: { min_duration_ms: "412" } });
  });

  it("says the raw data aged out instead of opening an empty screen (AC22)", () => {
    const links = lookNext({ ...source, raw_kept_until: "2026-11-09T14:59:00.000Z" }, NOW);

    expect(links.slice(0, 3).map((link) => link.kind)).toEqual([
      "aged_out",
      "aged_out",
      "aged_out",
    ]);
    expect(links[0].label).toBe(`Raw traces aged out on ${formatDate("2026-11-09T14:59:00.000Z")}`);
    expect(links.slice(3).map((link) => link.kind)).toEqual(["link", "link"]);
  });
});

describe("a fact's link (FR16, AC105)", () => {
  it("opens an error group at its window", () => {
    const link = factLink(
      { target: { kind: "error_group", group_id: "a1b2c3", services: ["checkout"] } },
      source,
      NOW,
    );

    expect(link).toMatchObject({
      kind: "link",
      to: "/errors",
      search: { ...WINDOW, group: "a1b2c3", service_name: "checkout" },
    });
  });

  it.each(fixture.cases)("builds the shared search for a callee that $name", (shape) => {
    const built = dependencySearch(shape.target as ExplanationFactTarget);

    expect(built).toEqual({ label: shape.label, search: shape.search });
  });

  it("carries the window beside a dependency's search", () => {
    const [shape] = fixture.cases;
    const link = factLink({ target: shape.target as ExplanationFactTarget }, source, NOW);

    expect(link).toMatchObject({ to: "/traces", search: { ...WINDOW, ...shape.search } });
  });

  it.each([
    [
      { kind: "deploy", release_id: "r-1" },
      { to: "/releases/$releaseId", params: { releaseId: "r-1" } },
    ],
    [{ kind: "deploy" }, { to: "/releases", search: WINDOW }],
    [{ kind: "host" }, { to: "/infrastructure", label: "Infrastructure (whole project)" }],
    [
      { kind: "finding", id: "f-2" },
      { to: "/findings/$findingId", params: { findingId: "f-2" } },
    ],
    [
      { kind: "check", id: "c-1" },
      { to: "/synthetics/$checkId", params: { checkId: "c-1" } },
    ],
    [{ kind: "incident", id: "i-1" }, { to: "/alerts/incidents/$incidentId" }],
  ])("opens %o where it lives", (target, expected) => {
    expect(factLink({ target: target as ExplanationFactTarget }, source, NOW)).toMatchObject(
      expected,
    );
  });

  it("has none for a fact with nothing to open", () => {
    expect(factLink({ target: null }, source, NOW)).toBeNull();
    expect(factLink({ target: { kind: "finding" } }, source, NOW)).toBeNull();
  });

  it("says a dependency's traces aged out rather than linking (AC22)", () => {
    const [shape] = fixture.cases;
    const link = factLink(
      { target: shape.target as ExplanationFactTarget },
      { ...source, raw_kept_until: "2026-11-01T00:00:00.000Z" },
      NOW,
    );

    expect(link).toEqual({
      kind: "aged_out",
      label: `Raw traces aged out on ${formatDate("2026-11-01T00:00:00.000Z")}`,
    });
  });
});

// @vitest-environment jsdom

import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { formatZonedTime } from "@/shared/format/zoned";
import type { OverviewPoint } from "@/shared/overview-plots";

import type { ClosedReason, DetectionStatus, FeedFinding, Finding, ProjectTotals } from "./api";
import type { FindingsViewProps } from "./FindingsScreen";
import { FindingsView } from "./FindingsScreen";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

const TOTALS: ProjectTotals = {
  service_count: 11,
  error_count: 6_048,
  requests_per_minute: 8_400,
  error_rate: 0.012,
  p95_ms: 210,
};

const FINDING: Finding = {
  id: "f1",
  project_id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a00aa",
  scope: "service",
  service_name: "checkout-api",
  title: "Checkout is 4× slower than normal since 15:04",
  severity: "critical",
  state: "open",
  closed_reason: null,
  service_snoozed_until: null,
  service_muted_until: null,
  muted: false,
  started_at: "2026-09-13T15:04:00Z",
  closed_at: null,
  acknowledged_at: null,
  signals: [
    {
      signal: "slower",
      key: "slower",
      normal: 340,
      band_low: 340,
      band_high: 510,
      current: 1_360,
      worst: 3_900,
      unit: "ms",
      comparison: "4× slower than normal",
      endpoint: "POST /checkout",
      endpoint_current: null,
      endpoint_last_week: null,
    },
  ],
  trend: [1, 2, 3, 5, 7, 8, 7],
  case_state: null,
  case_resolution: null,
  case_opened_at: null,
  case_resolved_at: null,
  root_finding_id: null,
  case_signal: "open",
  case_severity: "warning",
  open_joined_finding_id: null,
  open_joined_since: null,
};

const closed = (reason: ClosedReason | null, id = "closed"): Finding => ({
  ...FINDING,
  id,
  state: "closed",
  closed_reason: reason,
  closed_at: "2026-09-13T16:00:00Z",
});

const BASE: FindingsViewProps = {
  findings: [],
  mutedCount: 0,
  showingMuted: false,
  onToggleMuted: () => {},
  isPending: false,
  isError: false,
  onRetry: () => {},
  totals: TOTALS,
  checks: { total: 3, passing: 3, down: 0 },
};

/** Inside a router, as the app mounts it: each card links to its finding's page. */
async function render(overrides: Partial<FindingsViewProps> = {}): Promise<HTMLElement> {
  const rootRoute = createRootRoute({
    component: () => createElement(FindingsView, { ...BASE, ...overrides }),
  });
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/findings/$findingId",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));
  return container;
}

const readable = async (overrides: Partial<FindingsViewProps> = {}) =>
  ((await render(overrides)).textContent ?? "").replace(/\s+/g, " ").trim();

describe("AC18: healthy and failed-to-load must never look alike", () => {
  it("reports what it checked when the feed was actually read", async () => {
    const text = await readable();

    expect(text).toContain("All 3 synthetic checks are passing");
  });

  it("never claims overall health, even with errors on screen beside it", async () => {
    // Nothing assesses the system yet, so nothing on this screen may say it is fine.
    const text = await readable({ totals: { ...TOTALS, error_count: 8100, error_rate: 0.9 } });

    expect(text).not.toContain("All systems normal");
    expect(text).toContain("90");
  });

  it("never claims health when the feed could not be read", async () => {
    // The single most dangerous failure on this screen: telling a customer their
    // system is fine when the truth is that we cannot see it.
    const text = await readable({ isError: true, error: new Error("boom") });

    expect(text).not.toContain("synthetic checks are passing");
    expect(text).toContain("could not be loaded");
    expect(text).toContain("not the same as nothing being wrong");
  });

  it("carries none of the healthy state's reassurance when the read failed", async () => {
    // Checked phrase by phrase rather than as one string, so an edit that puts any
    // single piece of the healthy state back into the error state fails here. The
    // summary counts too: traffic numbers beside a failure still read as "we can
    // see your system", and we cannot.
    const failed = await readable({ isError: true, error: new Error("boom") });

    for (const reassurance of [
      "synthetic checks are passing",
      "services reporting",
      "checks passing",
      "8,400",
      "210ms",
    ]) {
      expect(failed).not.toContain(reassurance);
    }
    expect(await readable()).toContain("synthetic checks are passing");
  });

  it("waits with a skeleton rather than a spinner or a blank page", async () => {
    // F1 AC1.7 forbids both by name.
    const container = await render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading findings"]')).not.toBeNull();
    expect(container.textContent).not.toContain("synthetic checks are passing");
  });
});

describe("the healthy state carries the evidence for its own claim", () => {
  it("shows the RED summary, which is the content while nothing assesses it", async () => {
    const text = await readable();

    expect(text).toContain("8,400");
    expect(text).toContain("210ms");
    expect(text).toContain("11 services reporting");
  });

  it("names how many synthetic checks are passing", async () => {
    const text = await readable({ checks: { total: 3, passing: 2, down: 0 } });

    expect(text).toContain("2 of 3");
  });

  it("names the checks that are down (D1)", async () => {
    // The evidence and the headline are read from the same numbers now, so the
    // screen cannot contradict itself the way it did on a project whose four
    // checks were all failing.
    const text = await readable({ checks: { total: 4, passing: 0, down: 4 } });

    expect(text).not.toContain("synthetic checks are passing");
    expect(text).toContain("4 checks are down");
    expect(text).toContain("0 of 4");
  });

  it("does not invent a summary when nothing has reported", async () => {
    // A screen of zeroes is what an outage looks like.
    const text = await readable({ totals: undefined, checks: undefined });

    expect(text).toContain("Nothing has reported to this project yet");
    expect(text).not.toContain("8,400");
  });
});

describe("the feed leads when there is anything in it", () => {
  it("drops the summary once a finding exists", async () => {
    // When findings exist they lead and the summary drops away; when they do not,
    // the summary is the content.
    const text = await readable({ findings: [FINDING] });

    expect(text).toContain("Checkout is 4× slower than normal since 15:04");
    expect(text).not.toContain("synthetic checks are passing");
  });

  it("says when the finding's service is snoozed or muted, after a reload too", async () => {
    const until = new Date(Date.now() + 30 * 60_000).toISOString();
    const snoozed = await readable({ findings: [{ ...FINDING, service_snoozed_until: until }] });
    const muted = await readable({ findings: [{ ...FINDING, service_muted_until: until }] });

    expect(snoozed).toContain("Snoozed");
    expect(muted).toContain("Muted");
  });

  it("reads as a sentence with the service beneath it", async () => {
    const text = await readable({ findings: [FINDING] });

    expect(text).toContain("checkout-api");
  });

  it("names the project when the finding spans several services", async () => {
    const text = await readable({
      findings: [{ ...FINDING, scope: "project", service_name: null }],
    });

    expect(text).toContain("This project");
  });

  it("names each open finding's severity in a word, not by its colour alone", async () => {
    const container = await render({
      findings: [FINDING, { ...FINDING, id: "f2", severity: "warning" }],
    });

    const rows = [...container.querySelectorAll("li")].map((row) =>
      (row.textContent ?? "").replace(/\s+/g, " "),
    );
    expect(rows[0]).toContain("Critical");
    expect(rows[1]).toContain("Warning");
  });

  it("says when the feed shows only the first 200", async () => {
    expect(await readable({ findings: [FINDING], truncated: true })).toContain(
      "Showing the first 200 findings",
    );
    expect(await readable({ findings: [FINDING] })).not.toContain("Showing the first 200");
  });

  it("offers no probable cause until Stage 2 can name one", async () => {
    expect(await readable({ findings: [FINDING] })).not.toContain("Probably");
  });

  describe("the empty feed's sample and dates (F3 FR46, FR55; AC84, AC104)", () => {
    it("links to the labelled sample and says how deploy facts start", async () => {
      const container = await render({ findings: [] });
      const sample = [...container.querySelectorAll("a")].find(
        (link) => link.textContent === "See a sample explanation",
      );

      expect(sample?.getAttribute("href")).toBe("/findings/sample");
      expect(container.textContent).toContain("send deploys to get deploy facts");
    });

    it("gives the first findings' date from the project's own data", async () => {
      const container = await render({
        findings: [],
        detection: {
          state: "learning",
          services_total: 2,
          services_learning: 2,
          services_judged: 0,
          services_delayed: 0,
          services_muted: 0,
          earliest_judging_date: "2026-11-16",
          not_running_since: null,
          delayed_since: null,
          open_findings: 0,
          services_too_many: 0,
          judged_service_limit: 50,
        },
      });

      expect(container.textContent).toContain("First findings from 16 Nov");
    });
  });

  describe("the card's likely cause (F3 FR18, AC67, AC75)", () => {
    const deploy = [
      { text: "Likely: deploy of " },
      { name: "checkout" },
      { text: " " },
      { name: "v2.1" },
      { text: " (3 in 7 days)" },
    ];
    const cardOf = async (finding: FeedFinding) =>
      (await render({ findings: [finding] })).querySelector('[data-testid="card-cause"]');

    it("states a deploy cause with its rate, and wraps rather than truncating", async () => {
      const line = await cardOf({ ...FINDING, likely_cause_sentence: deploy });

      expect(line?.textContent).toBe("Likely: deploy of checkout v2.1 (3 in 7 days)");
      expect(line?.className).not.toContain("truncate");
    });

    it("says 'from telemetry only' and never 'Likely' for a possible cause", async () => {
      const possible = await cardOf({
        ...FINDING,
        likely_cause_sentence: [
          { text: "Possibly, from telemetry only: calls to " },
          { name: "payments" },
        ],
      });

      expect(possible?.textContent).toContain("from telemetry only");
      expect(possible?.textContent).not.toContain("Likely");
    });

    it("has no line with no cause, or for a closed finding", async () => {
      expect(await cardOf({ ...FINDING, likely_cause_sentence: null })).toBeNull();
      expect(await cardOf({ ...closed("recovered"), likely_cause_sentence: deploy })).toBeNull();
    });

    it("shows a name as text, never a link, and carries no planted wording", async () => {
      const container = await render({
        findings: [
          {
            ...FINDING,
            likely_cause_sentence: [{ text: "Likely: calls to " }, { name: "http://evil.example" }],
            wording: "WORDING-MARKER",
          } as FeedFinding,
        ],
      });
      const line = container.querySelector('[data-testid="card-cause"]');

      expect(line?.querySelector("a, img")).toBeNull();
      expect(container.textContent).not.toContain("WORDING-MARKER");
    });
  });

  it("gives a closed finding one quiet line with no trend to read", async () => {
    const container = await render({ findings: [closed("recovered")] });

    expect(container.textContent).toContain("Recovered");
    expect(container.querySelector('svg[role="img"]')).toBeNull();
  });

  it("puts anything still wrong above anything already closed", async () => {
    const container = await render({
      findings: [closed("recovered"), { ...FINDING, id: "broken" }],
    });

    const rows = [...container.querySelectorAll("li")].map((row) => row.textContent ?? "");
    expect(rows[0]).not.toContain("Recovered");
    expect(rows[1]).toContain("Recovered");
  });
});

describe("a closed finding names how it closed", () => {
  // A finding that closed because its service went quiet, or because nobody answered,
  // was not fixed. "Resolved" would say it was.
  const WORDS: Record<ClosedReason, string> = {
    recovered: "Recovered",
    false_alarm: "Closed as a false alarm",
    new_normal: "Accepted as the new normal",
    adopted: "Accepted as the new normal after 7 days with no answer",
    service_gone: "Service stopped sending",
    could_not_be_judged: "Closed: too quiet to judge for 6 hours",
  };

  // The row's words: the filter's option *Resolved cases* (F22 FR22) is about cases,
  // which a person resolves, and is not a claim about this finding.
  const rowText = async (finding: Finding) =>
    ((await render({ findings: [finding] })).querySelector("ul")?.textContent ?? "")
      .replace(/\s+/g, " ")
      .trim();

  for (const [reason, words] of Object.entries(WORDS) as [ClosedReason, string][]) {
    it(`says "${words}" for ${reason}, never "Resolved"`, async () => {
      const text = await rowText(closed(reason));

      expect(text).toContain(words);
      expect(text).not.toMatch(/resolved/i);
    });
  }

  it("still says it closed when the reason is missing", async () => {
    const text = await rowText(closed(null));

    expect(text).toContain("Closed");
    expect(text).not.toMatch(/resolved/i);
  });
});

describe("muted findings stay out of the feed", () => {
  it("offers a quiet link to them when there are any", async () => {
    const onToggleMuted = vi.fn();
    const container = await render({ findings: [FINDING], mutedCount: 2, onToggleMuted });

    const link = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Muted (2)",
    );
    expect(link).toBeDefined();
    act(() => link?.click());
    expect(onToggleMuted).toHaveBeenCalledOnce();
  });

  it("offers the link under the summary too, so an empty feed does not hide them", async () => {
    const text = await readable({ mutedCount: 1 });

    expect(text).toContain("All 3 synthetic checks are passing");
    expect(text).toContain("Muted (1)");
  });

  it("shows no link when nothing is muted", async () => {
    expect(await readable({ findings: [FINDING] })).not.toContain("Muted");
  });

  it("does not show the summary when the muted list is empty", async () => {
    // An empty muted list says nothing about the system's health.
    const text = await readable({ showingMuted: true });

    expect(text).toContain("No finding is muted");
    expect(text).toContain("Back to the feed");
    expect(text).not.toContain("synthetic checks are passing");
  });

  it("offers no link when the feed could not be read", async () => {
    expect(
      await readable({ isError: true, error: new Error("boom"), mutedCount: 2 }),
    ).not.toContain("Muted");
  });
});

describe("detection's line leads the feed (C18)", () => {
  const JUDGING: DetectionStatus = {
    state: "judging",
    services_total: 3,
    services_learning: 0,
    services_judged: 3,
    services_delayed: 0,
    services_muted: 0,
    earliest_judging_date: null,
    not_running_since: null,
    delayed_since: null,
    open_findings: 0,
    services_too_many: 0,
    judged_service_limit: 500,
  };
  const NOT_RUNNING: DetectionStatus = {
    ...JUDGING,
    state: "not_running",
    not_running_since: "2026-10-31T14:02:00Z",
  };

  it("sits above the checks' line, which stays", async () => {
    const text = await readable({ detection: JUDGING, zone: "UTC" });

    const detection = text.indexOf("All judged services are normal");
    const checksLine = text.indexOf("All 3 synthetic checks are passing");
    expect(detection).toBeGreaterThanOrEqual(0);
    expect(checksLine).toBeGreaterThan(detection);
    expect(text).not.toContain("All systems normal");
  });

  it("says it is learning, not that anything is normal, on a new project (AC42)", async () => {
    const text = await readable({
      detection: {
        ...JUDGING,
        state: "learning",
        services_learning: 3,
        services_judged: 0,
        earliest_judging_date: "2026-11-03",
      },
    });

    expect(text).toContain("Learning your services' normal. Judging starts on 3 Nov");
    expect(text).not.toMatch(/all judged services are normal|all systems normal/i);
  });

  it("says detection is not running, with its clock named, even over findings (AC26)", async () => {
    const text = await readable({ detection: NOT_RUNNING, zone: "UTC", findings: [FINDING] });

    expect(text).toContain("Detection has not run since");
    expect(text).toContain("14:02 UTC");
    expect(text).not.toContain("services are normal");
  });

  it("says services past the cap are not judged, above the findings (design, *Security*)", async () => {
    const text = await readable({
      detection: { ...JUDGING, state: "too_many_services", services_too_many: 12 },
      findings: [FINDING],
    });

    expect(text).toContain(
      "Too many services to judge all: the 500 busiest are judged, and 12 are not",
    );
    expect(text.indexOf("Too many services")).toBeLessThan(text.indexOf(FINDING.title));
  });

  it("shows the error state, never a healthy line, when a read failed", async () => {
    // The screen folds a failed status read into `isError` (`FindingsScreen.tsx`).
    const text = await readable({ detection: JUDGING, isError: true, error: new Error("boom") });

    expect(text).toContain("could not be loaded");
    expect(text).not.toMatch(/normal/i);
  });

  it("leaves the line out of the muted list, which says nothing about the system", async () => {
    expect(await readable({ detection: NOT_RUNNING, showingMuted: true })).not.toContain(
      "Detection",
    );
  });
});

describe("FR3: the front door shows proof that telemetry arrived", () => {
  const POINTS: OverviewPoint[] = [
    {
      bucket: "2026-09-13T10:00:00Z",
      requests_per_minute: 8_000,
      error_rate: 0.01,
      p50_ms: 40,
      p95_ms: 200,
      p99_ms: 900,
    },
    {
      bucket: "2026-09-13T10:01:00Z",
      requests_per_minute: 8_800,
      error_rate: 0.014,
      p50_ms: 44,
      p95_ms: 220,
      p99_ms: 1_060,
    },
  ];

  it("draws Overview's three charts under the summary when telemetry exists and the feed is empty (AC3)", async () => {
    const text = await readable({ points: POINTS });

    for (const panel of ["Rate", "Errors", "Duration"]) expect(text).toContain(panel);
  });

  it("links prominently to Overview (AC3)", async () => {
    const container = await render({ points: POINTS });

    expect(container.querySelector('a[href="/overview"]')).not.toBeNull();
  });

  it("keeps the charts out of the feed once a finding exists", async () => {
    const text = await readable({ points: POINTS, findings: [FINDING] });

    expect(text).not.toContain("Duration");
  });

  it("says nothing has reported in this window, with no readings and no charts (AC3)", async () => {
    // `/services/overview` answers zeros with `service_count: 0`, the API's real
    // shape. A screen of zeroes reads as an outage, so the readings and the
    // charts go, and the words say how to start and to widen the window.
    const container = await render({
      totals: {
        service_count: 0,
        error_count: 0,
        requests_per_minute: 0,
        error_rate: 0,
        p95_ms: 0,
      },
      checks: undefined,
      points: [],
    });
    const text = (container.textContent ?? "").replace(/\s+/g, " ").trim();

    expect(text).toContain("Nothing has reported in this window");
    expect(text).not.toContain("0 services reporting");
    expect(text).not.toContain("Requests/min");
    expect(text).not.toContain("Duration");
    expect(text).toContain("widen");
    expect(container.querySelector('a[href="/docs"]')).not.toBeNull();
  });
});

describe("D12: a finding opens its own page", () => {
  it("links each card, open or closed, to its finding", async () => {
    const container = await render({ findings: [FINDING, closed("recovered", "f2")] });

    expect(container.querySelector('a[href="/findings/f1"]')?.textContent).toBe(FINDING.title);
    expect(container.querySelector('a[href="/findings/f2"]')?.textContent).toBe(FINDING.title);
  });
});

// ------------------------------------------------------- the case filters (F22 T2.4)

const ASHA = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0009";

// Today, so the sentence's times carry no day (FR50): each test reads them through
// the same zoned formatter the screen uses.
const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();
const CLOSED_AT = hoursAgo(0.5);
const BACK_AT = hoursAgo(0.25);
const at = (iso: string) => formatZonedTime(iso, { zone: "UTC" });

const caseRow = (overrides: Partial<FeedFinding> = {}): FeedFinding => ({
  ...FINDING,
  id: "case-1",
  state: "closed",
  closed_reason: "recovered",
  closed_at: CLOSED_AT,
  severity: "warning",
  case_severity: "warning",
  case_state: "investigating",
  case_opened_at: "2026-09-13T15:10:00Z",
  case_signal: "closed",
  assignee_id: ASHA,
  assignee_name: "Asha",
  case_taken_at: "2026-09-13T15:10:00Z",
  project_name: "Staging",
  trend: [],
  ...overrides,
});

describe("a case filter reads differently from the feed (AC109, AC128, FR22)", () => {
  it("shows no detection line, no summary and no muted link, and says it reads every project", async () => {
    const text = await readable({
      caseFilter: "active",
      findings: [caseRow()],
      mutedCount: 4,
      detection: {
        state: "not_running",
        services_total: 2,
        services_learning: 0,
        services_judged: 2,
        services_delayed: 0,
        services_muted: 0,
        earliest_judging_date: null,
        not_running_since: "2026-09-13T14:02:00Z",
        delayed_since: null,
        open_findings: 0,
        services_too_many: 0,
        judged_service_limit: 500,
      },
    });

    expect(text).toContain("Across all your projects");
    expect(text).not.toContain("Detection has not run");
    expect(text).not.toContain("Muted (4)");
    expect(text).not.toContain("8,400");
  });

  it.each([
    ["active", "No active cases in your organization."],
    ["mine", "Nothing is assigned to you."],
    ["resolved", "No resolved cases are kept."],
  ] as const)(
    "names the %s filter when it is empty, offers Show all, and never the summary",
    async (filter, words) => {
      const container = await render({ caseFilter: filter, findings: [] });
      const text = (container.textContent ?? "").replace(/\s+/g, " ");

      expect(text).toContain(words);
      expect(text).not.toContain("synthetic checks are passing");
      expect(text).not.toContain("services reporting");
      expect([...container.querySelectorAll("button")].map((each) => each.textContent)).toContain(
        "Show all",
      );
    },
  );

  it("says when it shows only the newest 200", async () => {
    expect(
      await readable({ caseFilter: "resolved", findings: [caseRow()], truncated: true }),
    ).toContain("Showing the newest 200.");
  });
});

describe("a case row is the full statement (FR23, design *The feed*)", () => {
  it("states both questions in one sentence, names the project, and draws no sparkline", async () => {
    const container = await render({ caseFilter: "active", findings: [caseRow()], zone: "UTC" });
    const text = (container.textContent ?? "").replace(/\s+/g, " ");

    expect(text).toContain(`Recovered at ${at(CLOSED_AT)}, still being investigated by Asha`);
    expect(text).toContain("Warning");
    expect(text).toContain("Staging");
    expect(container.querySelector('[role="img"]')).toBeNull();
    expect(text).not.toContain("Acked");
  });

  it("never reads Recovered for a root whose finding came back, at the worse severity (AC59)", async () => {
    const back = caseRow({
      case_signal: "open",
      case_severity: "critical",
      open_joined_finding_id: "returning",
      open_joined_since: BACK_AT,
      assignee_acted_since_join: true,
    });
    for (const filter of ["active", null] as const) {
      const text = await readable({ caseFilter: filter, findings: [back], zone: "UTC" });
      expect(text).toContain(`Came back at ${at(BACK_AT)} and is still open`);
      expect(text).not.toContain("Recovered at");
      expect(text).toContain("Critical");
    }
  });

  it("warns in the last 14 days before an idle case ends itself (FR26)", async () => {
    const idle = caseRow({
      case_activity_at: "2026-07-29T16:00:00Z",
      case_ends_on: "2026-10-27T16:00:00Z",
      case_ends_by: "idle",
    });
    expect(await readable({ caseFilter: "active", findings: [idle], zone: "UTC" })).toContain(
      "Ends by itself on 27 Oct, 16:00 UTC. Add a note to keep it going, or resolve it.",
    );
  });

  it("offers Resolve on Active cases and Assigned to me only, to someone who may (AC91, FR52)", async () => {
    const offered = async (overrides: Partial<FindingsViewProps>) =>
      [
        ...(
          await render({ findings: [caseRow()], canResolve: true, ...overrides })
        ).querySelectorAll("button"),
      ]
        .map((each) => each.textContent)
        .includes("Resolve");

    expect(await offered({ caseFilter: "active" })).toBe(true);
    expect(await offered({ caseFilter: "mine" })).toBe(true);
    expect(
      await offered({ caseFilter: "resolved", findings: [caseRow({ case_state: "resolved" })] }),
    ).toBe(false);
    expect(await offered({ caseFilter: null })).toBe(false);
    expect(await offered({ caseFilter: "active", canResolve: false })).toBe(false);
  });

  it("resolves at one tap with the signal closed, and asks first while it is open (FR6)", async () => {
    const resolved: string[] = [];
    const closed = await render({
      caseFilter: "active",
      findings: [caseRow()],
      canResolve: true,
      onResolve: (id) => resolved.push(id),
    });
    await act(async () => {
      [...closed.querySelectorAll("button")]
        .find((each) => each.textContent === "Resolve")
        ?.click();
    });
    expect(resolved).toEqual(["case-1"]);

    const open = await render({
      caseFilter: "active",
      findings: [caseRow({ id: "case-2", case_signal: "open", state: "open", closed_at: null })],
      canResolve: true,
      onResolve: (id) => resolved.push(id),
    });
    await act(async () => {
      [...open.querySelectorAll("button")].find((each) => each.textContent === "Resolve")?.click();
    });
    expect(resolved).toEqual(["case-1"]);
    expect(open.textContent).toContain("The signal is still open");
  });
});

describe("the feed keeps working cases in sight (FR21, FR23)", () => {
  it("says what comes first, in the new order's words", async () => {
    expect(await readable()).toContain(
      "Anything still wrong or still being worked comes before anything finished.",
    );
  });

  it("gives a closed finding with an active case the full statement, not one quiet line", async () => {
    const text = await readable({ findings: [caseRow()], zone: "UTC" });
    expect(text).toContain(`Recovered at ${at(CLOSED_AT)}, still being investigated by Asha`);
  });

  it("links a joined finding to the case it is part of", async () => {
    const container = await render({
      findings: [{ ...closed("recovered", "joined-1"), root_finding_id: "root-1" }],
    });
    const link = [...container.querySelectorAll("a")].find(
      (each) => each.textContent === "Part of a case",
    );
    expect(link?.getAttribute("href")).toBe("/findings/root-1");
  });
});

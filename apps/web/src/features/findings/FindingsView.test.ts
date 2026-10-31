// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ClosedReason, DetectionStatus, Finding, ProjectTotals } from "./api";
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
  muted: false,
  started_at: "2026-09-13T15:04:00Z",
  closed_at: null,
  acknowledged_at: null,
  signals: [
    {
      signal: "slower",
      normal: 340,
      current: 1_360,
      worst: 3_900,
      unit: "ms",
      comparison: "4× slower than normal",
      endpoint: "POST /checkout",
    },
  ],
  trend: [1, 2, 3, 5, 7, 8, 7],
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

function render(overrides: Partial<FindingsViewProps> = {}): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() => root.render(createElement(FindingsView, { ...BASE, ...overrides })));
  return container;
}

const readable = (overrides: Partial<FindingsViewProps> = {}) =>
  (render(overrides).textContent ?? "").replace(/\s+/g, " ").trim();

describe("AC18: healthy and failed-to-load must never look alike", () => {
  it("reports what it checked when the feed was actually read", () => {
    const text = readable();

    expect(text).toContain("All 3 synthetic checks are passing");
  });

  it("never claims overall health, even with errors on screen beside it", () => {
    // Nothing assesses the system yet, so nothing on this screen may say it is fine.
    const text = readable({ totals: { ...TOTALS, error_count: 8100, error_rate: 0.9 } });

    expect(text).not.toContain("All systems normal");
    expect(text).toContain("90");
  });

  it("never claims health when the feed could not be read", () => {
    // The single most dangerous failure on this screen: telling a customer their
    // system is fine when the truth is that we cannot see it.
    const text = readable({ isError: true, error: new Error("boom") });

    expect(text).not.toContain("synthetic checks are passing");
    expect(text).toContain("could not be loaded");
    expect(text).toContain("not the same as nothing being wrong");
  });

  it("carries none of the healthy state's reassurance when the read failed", () => {
    // Checked phrase by phrase rather than as one string, so an edit that puts any
    // single piece of the healthy state back into the error state fails here. The
    // summary counts too: traffic numbers beside a failure still read as "we can
    // see your system", and we cannot.
    const failed = readable({ isError: true, error: new Error("boom") });

    for (const reassurance of [
      "synthetic checks are passing",
      "services reporting",
      "checks passing",
      "8,400",
      "210ms",
    ]) {
      expect(failed).not.toContain(reassurance);
    }
    expect(readable()).toContain("synthetic checks are passing");
  });

  it("waits with a skeleton rather than a spinner or a blank page", () => {
    // F1 AC1.7 forbids both by name.
    const container = render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading findings"]')).not.toBeNull();
    expect(container.textContent).not.toContain("synthetic checks are passing");
  });
});

describe("the healthy state carries the evidence for its own claim", () => {
  it("shows the RED summary, which is the content while nothing assesses it", () => {
    const text = readable();

    expect(text).toContain("8,400");
    expect(text).toContain("210ms");
    expect(text).toContain("11 services reporting");
  });

  it("names how many synthetic checks are passing", () => {
    const text = readable({ checks: { total: 3, passing: 2, down: 0 } });

    expect(text).toContain("2 of 3");
  });

  it("names the checks that are down (D1)", () => {
    // The evidence and the headline are read from the same numbers now, so the
    // screen cannot contradict itself the way it did on a project whose four
    // checks were all failing.
    const text = readable({ checks: { total: 4, passing: 0, down: 4 } });

    expect(text).not.toContain("synthetic checks are passing");
    expect(text).toContain("4 checks are down");
    expect(text).toContain("0 of 4");
  });

  it("does not invent a summary when nothing has reported", () => {
    // A screen of zeroes is what an outage looks like.
    const text = readable({ totals: undefined, checks: undefined });

    expect(text).toContain("Nothing has reported to this project yet");
    expect(text).not.toContain("8,400");
  });
});

describe("the feed leads when there is anything in it", () => {
  it("drops the summary once a finding exists", () => {
    // When findings exist they lead and the summary drops away; when they do not,
    // the summary is the content.
    const text = readable({ findings: [FINDING] });

    expect(text).toContain("Checkout is 4× slower than normal since 15:04");
    expect(text).not.toContain("synthetic checks are passing");
  });

  it("reads as a sentence with the service beneath it", () => {
    const text = readable({ findings: [FINDING] });

    expect(text).toContain("checkout-api");
  });

  it("names the project when the finding spans several services", () => {
    const text = readable({
      findings: [{ ...FINDING, scope: "project", service_name: null }],
    });

    expect(text).toContain("This project");
  });

  it("offers no probable cause until Stage 2 can name one", () => {
    expect(readable({ findings: [FINDING] })).not.toContain("Probably");
  });

  it("gives a closed finding one quiet line with no trend to read", () => {
    const container = render({ findings: [closed("recovered")] });

    expect(container.textContent).toContain("Recovered");
    expect(container.querySelector('svg[role="img"]')).toBeNull();
  });

  it("puts anything still wrong above anything already closed", () => {
    const container = render({ findings: [closed("recovered"), { ...FINDING, id: "broken" }] });

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

  for (const [reason, words] of Object.entries(WORDS) as [ClosedReason, string][]) {
    it(`says "${words}" for ${reason}, never "Resolved"`, () => {
      const text = readable({ findings: [closed(reason)] });

      expect(text).toContain(words);
      expect(text).not.toMatch(/resolved/i);
    });
  }

  it("still says it closed when the reason is missing", () => {
    const text = readable({ findings: [closed(null)] });

    expect(text).toContain("Closed");
    expect(text).not.toMatch(/resolved/i);
  });
});

describe("muted findings stay out of the feed", () => {
  it("offers a quiet link to them when there are any", () => {
    const onToggleMuted = vi.fn();
    const container = render({ findings: [FINDING], mutedCount: 2, onToggleMuted });

    const link = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Muted (2)",
    );
    expect(link).toBeDefined();
    act(() => link?.click());
    expect(onToggleMuted).toHaveBeenCalledOnce();
  });

  it("offers the link under the summary too, so an empty feed does not hide them", () => {
    const text = readable({ mutedCount: 1 });

    expect(text).toContain("All 3 synthetic checks are passing");
    expect(text).toContain("Muted (1)");
  });

  it("shows no link when nothing is muted", () => {
    expect(readable({ findings: [FINDING] })).not.toContain("Muted");
  });

  it("does not show the summary when the muted list is empty", () => {
    // An empty muted list says nothing about the system's health.
    const text = readable({ showingMuted: true });

    expect(text).toContain("No finding is muted");
    expect(text).toContain("Back to the feed");
    expect(text).not.toContain("synthetic checks are passing");
  });

  it("offers no link when the feed could not be read", () => {
    expect(readable({ isError: true, error: new Error("boom"), mutedCount: 2 })).not.toContain(
      "Muted",
    );
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
  };
  const NOT_RUNNING: DetectionStatus = {
    ...JUDGING,
    state: "not_running",
    not_running_since: "2026-10-31T14:02:00Z",
  };

  it("sits above the checks' line, which stays", () => {
    const text = readable({ detection: JUDGING, zone: "UTC" });

    const detection = text.indexOf("All judged services are normal");
    const checksLine = text.indexOf("All 3 synthetic checks are passing");
    expect(detection).toBeGreaterThanOrEqual(0);
    expect(checksLine).toBeGreaterThan(detection);
    expect(text).not.toContain("All systems normal");
  });

  it("says it is learning, not that anything is normal, on a new project (AC42)", () => {
    const text = readable({
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

  it("says detection is not running, with its clock named, even over findings (AC26)", () => {
    const text = readable({ detection: NOT_RUNNING, zone: "UTC", findings: [FINDING] });

    expect(text).toContain("Detection has not run since");
    expect(text).toContain("14:02 UTC");
    expect(text).not.toContain("services are normal");
  });

  it("shows the error state, never a healthy line, when a read failed", () => {
    // The screen folds a failed status read into `isError` (`FindingsScreen.tsx`).
    const text = readable({ detection: JUDGING, isError: true, error: new Error("boom") });

    expect(text).toContain("could not be loaded");
    expect(text).not.toMatch(/normal/i);
  });

  it("leaves the line out of the muted list, which says nothing about the system", () => {
    expect(readable({ detection: NOT_RUNNING, showingMuted: true })).not.toContain("Detection");
  });
});

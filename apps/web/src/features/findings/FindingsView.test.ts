// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type { Finding, ProjectTotals } from "./api";
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
  title: "Checkout is 4× slower than normal since 15:04",
  severity: "critical",
  state: "active",
  subject: "checkout-api · POST /checkout",
  impact: "~12% of checkout requests affected",
  probable_cause: "payment-gateway calls went from 340 ms to 3.9 s",
  started_at: "2026-09-13T15:04:00Z",
  resolved_at: null,
  trend: [1, 2, 3, 5, 7, 8, 7],
};

const BASE: FindingsViewProps = {
  findings: [],
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

  it("reads as a sentence with the entity beneath it", () => {
    const text = readable({ findings: [FINDING] });

    expect(text).toContain("checkout-api · POST /checkout");
    expect(text).toContain("~12% of checkout requests affected");
    expect(text).toContain("Probably: payment-gateway calls went from 340 ms to 3.9 s");
  });

  it("gives a resolved finding one quiet line with no trend to read", () => {
    const container = render({
      findings: [{ ...FINDING, state: "resolved", resolved_at: "2026-09-13T16:00:00Z" }],
    });

    expect(container.textContent).toContain("Resolved");
    expect(container.querySelector('svg[role="img"]')).toBeNull();
  });

  it("puts anything still wrong above anything already fixed", () => {
    const container = render({
      findings: [
        { ...FINDING, id: "fixed", state: "resolved", resolved_at: "2026-09-13T17:00:00Z" },
        { ...FINDING, id: "broken" },
      ],
    });

    const rows = [...container.querySelectorAll("li")].map((row) => row.textContent ?? "");
    expect(rows[0]).not.toContain("Resolved");
    expect(rows[1]).toContain("Resolved");
  });
});

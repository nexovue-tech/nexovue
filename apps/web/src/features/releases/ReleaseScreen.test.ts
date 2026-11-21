// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Block, Comparison, Release, Rollout, SenderDeploy, VersionErrors } from "./api";
import {
  json,
  minutesAgo,
  mountAt,
  readable,
  stubApi,
  unmountAll,
  waitFor,
} from "./release-harness.test-util";

/**
 * The whole release page (F28 T4.5): AC43, AC44, AC65, AC68, AC75, AC79, AC80, AC84, and
 * the sections it gained in Step 4. The clock is pinned, so no assertion depends on when
 * the suite runs.
 */

vi.mock("@/shared/components/TrendChart", () => ({
  CHART_HEIGHT: 132,
  TrendChart: (props: { label: readonly string[]; description: string }) =>
    createElement("div", {
      role: "img",
      "aria-label": props.description,
      "data-labels": props.label.join("|"),
    }),
}));

const NOW = new Date("2026-11-12T12:00:00Z");
const HOSTILE = ["<script>alert(1)</script>", "[click](https://evil.example)"];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

function release(overrides: Partial<Release> = {}): Release {
  return {
    id: "release-1",
    project_id: "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f",
    service_name: "checkout",
    version: "v2.1",
    basis: "both",
    activity: "serving",
    replaced_by: null,
    first_seen_at: minutesAgo(50),
    last_seen_at: minutesAgo(1),
    first_reported_at: minutesAgo(51),
    last_reported_at: minutesAgo(51),
    report_count: 1,
    last_state: "finished",
    periods: [
      {
        id: "period-1",
        started_at: minutesAgo(50),
        ended_at: null,
        start_basis: "seen",
        previous_version: "v2.0",
        already_running: false,
        is_return: false,
        later_findings: [
          {
            id: "finding-1",
            title: "checkout is slower than normal",
            severity: "warning",
            state: "open",
            started_at: minutesAgo(20),
          },
        ],
        later_findings_truncated: false,
      },
    ],
    deploys: [
      {
        id: "deploy-1",
        deployed_at: minutesAgo(80),
        received_at: minutesAgo(51),
        state: "finished",
        commit_sha: "a".repeat(40),
      },
    ],
    ...overrides,
  };
}

function block(overrides: Partial<Block> = {}): Block {
  return {
    kind: "rollout",
    method: "side_by_side",
    from: minutesAgo(50),
    to: minutesAgo(8),
    previous_from: minutesAgo(50),
    previous_to: minutesAgo(8),
    minutes_compared: 42,
    this_version: { version: "v2.1", requests: 6000, errors: 180, p95_ms: 412 },
    previous_version: { version: "v2.0", requests: 6000, errors: 12, p95_ms: 398 },
    failure_share: { reading: "higher", note: null },
    p95: { reading: "about_same", note: null },
    last_week: null,
    ...overrides,
  };
}

function comparison(overrides: Partial<Comparison> = {}): Comparison {
  return {
    period_id: "period-1",
    version: "v2.1",
    previous_version: "v2.0",
    reason: null,
    lead: "rollout",
    as_of: null,
    compared_to: minutesAgo(8),
    window_hours: 1,
    blocks: [block()],
    traffic: null,
    other_versions: [],
    numbers_kept_days: 30,
    incomplete: false,
    minutes_differing: 0,
    versions_combined: false,
    mixed_environments: false,
    finding: null,
    ...overrides,
  };
}

function errors(overrides: Partial<VersionErrors> = {}): VersionErrors {
  return {
    period_id: "period-1",
    version: "v2.1",
    previous_version: "v2.0",
    from: minutesAgo(50),
    to: minutesAgo(0),
    window_hours: 1,
    groups: [
      {
        group_id: "a".repeat(16),
        exception_type: "CardDeclined",
        message_template: "card <num> declined",
        kind: "new_here",
        this_occurrences: 4,
        previous_occurrences: 0,
        from_browser: false,
      },
      {
        group_id: "b".repeat(16),
        exception_type: "Timeout",
        message_template: "upstream timed out",
        kind: "gone_here",
        this_occurrences: 0,
        previous_occurrences: 2,
        from_browser: false,
      },
      {
        group_id: "c".repeat(16),
        exception_type: "KeyError",
        message_template: "missing key",
        kind: "in_both",
        this_occurrences: 1,
        previous_occurrences: 3,
        from_browser: false,
      },
    ],
    truncated: false,
    timed_out: false,
    aged_out_on: null,
    may_be_incomplete: false,
    browser_door_read: false,
    ...overrides,
  };
}

const ROLLOUT: Rollout = {
  period_id: "period-1",
  series: [
    { kind: "version", version: "v2.0" },
    { kind: "version", version: "v2.1" },
    { kind: "no_version", version: null },
    { kind: "combined", version: null },
    { kind: "other_versions", version: null },
  ],
  minutes: [{ minute: minutesAgo(9), requests: [50, 40, 5, 3, 2] }],
  as_of: null,
  compared_to: minutesAgo(8),
};

function sender(overrides: Partial<SenderDeploy> = {}): SenderDeploy {
  return {
    deploy_id: "deploy-1",
    deployed_at: minutesAgo(80),
    url: "https://ci.example/runs/41",
    source: "github-actions",
    metadata: { pipeline: "build 41" },
    metadata_withheld: false,
    ...overrides,
  };
}

function page(
  overrides: Partial<{
    release: unknown;
    comparison: ReturnType<typeof json>;
    errors: ReturnType<typeof json>;
    rollout: ReturnType<typeof json>;
    sender: ReturnType<typeof json>;
  }> = {},
) {
  return stubApi({
    "GET /releases/release-1": json(overrides.release ?? release()),
    "GET /releases/release-1/comparison": overrides.comparison ?? json(comparison()),
    "GET /releases/release-1/errors": overrides.errors ?? json(errors()),
    "GET /releases/release-1/rollout": overrides.rollout ?? json(ROLLOUT),
    "GET /releases/release-1/sender-details": overrides.sender ?? json({ deploys: [sender()] }),
  });
}

async function opened(path = "/releases/release-1") {
  const { container } = await mountAt(path);
  await waitFor(() => readable(container).includes("Facts"), "the facts");
  return container;
}

describe("the comparison section", () => {
  it("states how counts were made, and FR49's caution under about the same (AC79, AC80)", async () => {
    page();
    const container = await opened();
    await waitFor(() => readable(container).includes("Side by side over 42 minutes"), "sentence");
    expect(readable(container)).toContain("Counts assume you do not sample before sending.");
    expect(readable(container)).toContain(
      "This covers the whole service. A problem on one endpoint can be hidden here.",
    );
    const links = [...container.querySelectorAll("a")].map((link) => link.getAttribute("href"));
    expect(links).toContain("#errors-by-version");
    expect(links.some((href) => href?.startsWith("/services/checkout?from="))).toBe(true);
  });

  it("leaves FR49's caution out where no reading is about the same (AC79)", async () => {
    page({
      comparison: json(comparison({ blocks: [block({ p95: { reading: "higher", note: null } })] })),
    });
    const container = await opened();
    await waitFor(() => readable(container).includes("Side by side"), "the sentence");
    expect(readable(container)).not.toContain("This covers the whole service.");
  });

  it("gives higher critical text with the word, others dim ink, and never green (AC84)", async () => {
    page();
    const container = await opened();
    await waitFor(() => container.querySelector("td.text-critical-text"), "the higher cell");
    expect(container.querySelector("td.text-critical-text")?.textContent).toBe("higher");
    expect(
      [...container.querySelectorAll("td.text-ink-dim")].map((cell) => cell.textContent),
    ).toContain("about the same");
    expect(container.innerHTML).not.toMatch(/green|success/);
  });

  it("shows last week in before-and-after methods, traffic share side by side (AC65)", async () => {
    page({
      comparison: json(
        comparison({
          lead: "after_rollout",
          blocks: [
            block(),
            block({
              kind: "after_rollout",
              method: "version_before_after",
              last_week: { version: "", requests: 4000, errors: 12, p95_ms: 300 },
            }),
          ],
        }),
      ),
    });
    const container = await opened();
    await waitFor(() => readable(container).includes("Since the rollout finished"), "blocks");
    expect(readable(container)).toContain("Share of traffic");
    expect(readable(container)).toContain("Last week: 0.3% (12 of 4,000)");
    const headers = [...container.querySelectorAll("th")].map((cell) => cell.textContent);
    expect(headers.filter((text) => text === "Last week")).toHaveLength(1);
  });

  it("states each warning that is true", async () => {
    page({
      comparison: json(
        comparison({
          mixed_environments: true,
          versions_combined: true,
          incomplete: true,
          minutes_differing: 3,
        }),
      ),
    });
    const container = await opened();
    await waitFor(() => readable(container).includes("Incomplete"), "the warnings");
    expect(readable(container)).toContain("Two environments appear to share this project");
    expect(readable(container)).toContain("Versions were combined");
    expect(readable(container)).toContain("Incomplete: some counts were not stored for 3 minutes.");
  });

  it("opened from a finding, asks the API to choose the window and says what it chose (FR22)", async () => {
    const calls = page({
      comparison: json(
        comparison({
          window_hours: 6,
          finding: { id: "f-9", started_at: minutesAgo(10), reaches: true },
        }),
      ),
    });
    const container = await opened("/releases/release-1?period=period-1&finding=f-9");
    await waitFor(
      () => readable(container).includes("Compared over 6 hours, to include the finding"),
      "the window's sentence",
    );
    const asked = calls.find((url) => url.pathname.endsWith("/comparison"));
    expect(asked?.searchParams.get("finding")).toBe("f-9");
    expect(asked?.searchParams.get("window")).toBeNull();
    await waitFor(() => calls.find((url) => url.pathname.endsWith("/errors")), "the errors read");
    const errorsAsked = calls.find((url) => url.pathname.endsWith("/errors"));
    expect(errorsAsked?.searchParams.get("window")).toBe("6");
  });

  it("keeps the facts when the comparison fails (AC68)", async () => {
    page({ comparison: json({ title: "Unavailable", status: 503 }, 503) });
    const container = await opened();
    await waitFor(
      () => readable(container).includes("The comparison could not be read"),
      "the section's failure",
    );
    expect(readable(container)).toContain("Previous version");
    expect(readable(container)).toContain("Started after this release");
  });
});

describe("rollout share (FR26, AC65)", () => {
  it("draws the top four and the rest, with labels, and a table of the same shares", async () => {
    page();
    const container = await opened();
    const chart = await waitFor(() => container.querySelector("[role=img]"), "the chart");
    expect(chart.getAttribute("data-labels")).toBe(
      "v2.0|v2.1|No version|Combined versions|Other versions",
    );
    expect(chart.getAttribute("aria-label")).toContain("v2.0 50.0%");
    expect(readable(container)).toContain("Share in the latest minute");
  });
});

describe("errors by version (FR29 to FR31, AC69)", () => {
  it("lists new, gone and in both with the caution beside the list, each linked", async () => {
    page();
    const container = await opened();
    await waitFor(() => readable(container).includes("CardDeclined"), "the groups");
    for (const words of ["New here", "Gone here", "In both"]) {
      expect(readable(container)).toContain(words);
    }
    expect(readable(container)).toContain(
      "Not seen in the previous version during this window. That is not proof this version introduced it.",
    );
    const link = [...container.querySelectorAll("a")].find((a) => a.textContent === "Timeout");
    expect(link?.getAttribute("href")).toContain(`group=${"b".repeat(16)}`);
  });

  it("says a timed-out read in words, never as an empty list (AC68, FR51)", async () => {
    page({ errors: json(errors({ timed_out: true, groups: [] })) });
    const container = await opened();
    await waitFor(
      () => readable(container).includes("This read took too long. Try the 1-hour window."),
      "the timeout words",
    );
    expect(readable(container)).not.toContain("No errors in either version");
  });

  it("gives aged-out details their date, and sampling off its warning (FR30, FR31)", async () => {
    page({
      errors: json(
        errors({ groups: [], aged_out_on: "2026-11-01T12:00:00Z", may_be_incomplete: true }),
      ),
    });
    const container = await opened();
    await waitFor(() => readable(container).includes("Error details aged out on"), "the date");
    expect(readable(container)).toContain("this list may be incomplete");
  });
});

describe("facts and what the sender wrote", () => {
  it("leads with the CI's link, carrying no referrer, and shows each deploy's two times (AC44)", async () => {
    page();
    const container = await opened();
    const link = await waitFor(
      () => [...container.querySelectorAll("a")].find((a) => a.textContent?.includes("ci.example")),
      "the CI link",
    );
    expect(link.getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(link.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(readable(container)).toContain("Link from your CI");
    expect(readable(container)).toContain("the time your CI gave; it arrived");
    expect(readable(container)).toContain(`commit ${"a".repeat(40)}`);
  });

  it("never makes a link of a url that is not https", async () => {
    page({ sender: json({ deploys: [sender({ url: "javascript:alert(1)" })] }) });
    const container = await opened();
    await waitFor(() => readable(container).includes("What your CI sent"), "the section");
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
  });

  it("renders hostile names and sender text as plain text (AC43)", async () => {
    page({
      release: release({ version: HOSTILE[0] ?? "", service_name: HOSTILE[1] ?? "" }),
      sender: json({
        deploys: [sender({ source: HOSTILE[0] ?? "", metadata: { note: HOSTILE[1] ?? "" } })],
      }),
    });
    const container = await opened();
    await waitFor(() => readable(container).includes("What your CI sent"), "the section");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a[href='https://evil.example']")).toBeNull();
    expect(readable(container)).toContain("[click](https://evil.example)");
  });

  it("tells a viewer the details are for members, never showing them", async () => {
    page({ sender: json({ deploys: [sender({ metadata: null, metadata_withheld: true })] }) });
    const container = await opened();
    await waitFor(
      () => readable(container).includes("Only members and above can read the details"),
      "the withheld words",
    );
    expect(readable(container)).not.toContain("build 41");
  });

  it("keeps a 200-character version whole in the facts, breaking anywhere (AC75)", async () => {
    const long = "v".repeat(200);
    page({ release: release({ version: long }) });
    const container = await opened();
    const whole = [...container.querySelectorAll("span.break-all")].find(
      (span) => span.textContent === long,
    );
    expect(whole).toBeDefined();
    expect(container.querySelector("h1 span")?.getAttribute("title")).toBe(long);
    expect(container.querySelector("h1")?.textContent?.length).toBeLessThan(60);
  });

  it("lists findings that started after the release, linked", async () => {
    page();
    const container = await opened();
    const link = [...container.querySelectorAll("a")].find(
      (a) => a.textContent === "checkout is slower than normal",
    );
    expect(link?.getAttribute("href")).toBe("/findings/finding-1");
  });

  it("answers an unknown or another tenant's release with the not-found page", async () => {
    stubApi({ "GET /releases/release-1": json({ title: "No such release", status: 404 }, 404) });
    const { container } = await mountAt("/releases/release-1");
    await waitFor(() => readable(container).includes("No such release"), "not found");
  });
});

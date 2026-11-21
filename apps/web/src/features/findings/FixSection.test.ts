// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TestRole } from "@/shared/identity.test-util";

import type { FindingFixes, ReleaseFact } from "./api";
import { BRIEF_WORDS, SUGGESTED_FIX } from "./fix-page";
import {
  finding,
  json,
  mountFinding,
  problem,
  readable,
  settle,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * F5's rungs 1 and 2 on the finding page (plan T1.4; spec FR1, FR4, FR5, FR5a; D54;
 * AC11a, AC37e's first rows). Only the network is replaced, and the clock is fixed.
 */

const NOW = new Date("2026-11-11T14:32:00Z");
const BRIEF_TEXT =
  "These facts come from telemetry.\n\nEndpoint (reported by telemetry): POST /pay";

const fixes = (overrides: Partial<FindingFixes> = {}): FindingFixes => ({
  finding_id: "f-1",
  deploy_line: null,
  brief: { text: BRIEF_TEXT },
  section: null,
  ...overrides,
});

const RELEASE: ReleaseFact = {
  release_id: "r-21",
  period_id: "p-21",
  service_name: "checkout",
  version: "v2.1",
  started_at: "2026-11-11T13:58:00Z",
  start_basis: "reported",
  basis: "both",
  last_state: "finished",
  is_return: false,
  already_running: false,
  called: null,
};

/** A finding with F28's release 4 minutes before it, stated plainly. */
const withRelease = (before: ReleaseFact | null = RELEASE) =>
  finding({
    started_at: "2026-11-11T14:02:00Z",
    release_context: {
      before,
      coverage: "deploys_and_versions",
      last_reported_at: null,
      first_heard_at: null,
      nearby: [],
      called_known: true,
      affected: [],
    },
  });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

async function page(
  found: FindingFixes,
  { role = "owner" as TestRole, release = withRelease() } = {},
) {
  stubApi(release, { role, extra: { "GET /findings/f-1/fixes": json(found) } });
  const { container } = await mountFinding();
  await waitFor(() => container.querySelector("h1"), "the finding's sentence");
  for (let round = 0; round < 5; round += 1) await settle();
  return container;
}

const releaseRow = (container: Element) =>
  container.querySelector<HTMLElement>('section[aria-labelledby="finding-release"]');

describe("rung 1: F5's addition inside F28's row (FR1)", () => {
  const addition = {
    release_id: "r-21",
    earlier: { release_id: "r-20", version: "v2.0", last_state: "failed" as const },
    sentences: [
      [{ name: "v2.0" }, { text: " ran before it. CI's last report on it said it failed." }],
    ],
  };

  it("follows F28's sentence in the same row, with the earlier version's release", async () => {
    const container = await page(fixes({ deploy_line: addition }));

    const row = releaseRow(container);
    const text = readable(row ?? container);
    expect(text).toContain("v2.1 of checkout started 4 minutes before this.");
    expect(text).toContain("v2.0 ran before it. CI's last report on it said it failed.");
    expect(text.indexOf("v2.1 of checkout")).toBeLessThan(text.indexOf("v2.0 ran before it"));
    const link = [...(row?.querySelectorAll("a") ?? [])].find(
      (each) => readable(each) === "Open v2.0",
    );
    expect(link?.getAttribute("href")).toBe("/releases/r-20");
    expect(container.querySelectorAll('[aria-labelledby="finding-release"]')).toHaveLength(1);
  });

  it("adds nothing beside a different release than F28 names", async () => {
    const container = await page(fixes({ deploy_line: { ...addition, release_id: "r-other" } }));

    expect(readable(container)).not.toContain("ran before it");
  });
});

describe("rung 2: the brief (FR4, FR5, FR5a)", () => {
  it("is one line under the deploy line: label, warning, copy, and a closed disclosure", async () => {
    const container = await page(fixes());

    const brief = container.querySelector<HTMLElement>('section[aria-labelledby="finding-brief"]');
    expect(brief).not.toBeNull();
    expect(readable(brief as HTMLElement)).toContain(BRIEF_WORDS.label);
    expect(readable(brief as HTMLElement)).toContain(BRIEF_WORDS.warning);
    expect(brief?.querySelector("button")?.getAttribute("aria-label")).toBe("Copy brief");
    const details = brief?.querySelector("details");
    expect(details?.open).toBe(false);
    expect(details?.querySelector("summary")?.textContent).toBe(BRIEF_WORDS.disclosure);
    const text = container.querySelector('[data-testid="brief-text"]');
    expect(text?.textContent).toBe(BRIEF_TEXT);
    expect(text?.className).toContain("select-all");
  });

  it("D54: sits directly after F28's row and before F3's other facts", async () => {
    const container = await page(fixes());

    const row = releaseRow(container);
    const brief = container.querySelector('section[aria-labelledby="finding-brief"]');
    expect(row?.nextElementSibling).toBe(brief);
  });

  it("opens and takes focus when the clipboard write fails, so there is something to select", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    const container = await page(fixes());

    const copy = container.querySelector<HTMLButtonElement>(
      'section[aria-labelledby="finding-brief"] button',
    );
    copy?.click();
    await waitFor(
      () => document.activeElement?.getAttribute("data-testid") === "brief-text",
      "the brief's text to take focus",
    );

    expect(container.querySelector("details")?.open).toBe(true);
  });

  it("is not shown for a finding closed as not a problem", async () => {
    const container = await page(fixes({ brief: null }));

    expect(container.querySelector('section[aria-labelledby="finding-brief"]')).toBeNull();
    expect(readable(container)).not.toContain(BRIEF_WORDS.label);
  });

  it("says so in place, with a retry, when the read fails; the rest of the page works", async () => {
    stubApi(withRelease(), {
      extra: { "GET /findings/f-1/fixes": problem(500, "The database did not answer.") },
    });
    const { container } = await mountFinding();
    await waitFor(() => readable(container).includes(BRIEF_WORDS.notLoaded), "the failure");

    expect(readable(container)).toContain("The database did not answer.");
    expect(readable(releaseRow(container) ?? container)).toContain("v2.1 of checkout");
  });
});

describe("the Suggested fix section (D54, AC37e, AC11a)", () => {
  it.each<TestRole>(["owner", "admin", "member", "viewer"])(
    "renders no section, no heading and no jump link when %s has no line",
    async (role) => {
      const container = await page(fixes(), { role });

      expect(container.querySelector(`#${SUGGESTED_FIX.anchor}`)).toBeNull();
      expect(readable(container)).not.toContain(SUGGESTED_FIX.jump);
      expect([...container.querySelectorAll("h2")].map(readable)).not.toContain(
        SUGGESTED_FIX.heading,
      );
    },
  );

  it.each<TestRole>(["member", "viewer"])(
    "AC11a: with AI off, a %s's page does not mention AI",
    async (role) => {
      const container = await page(fixes(), { role });

      expect(readable(container)).not.toMatch(/\bAI\b/);
    },
  );

  it("D54: a line renders full width below the split, with a jump link beside Evidence", async () => {
    const container = await page(
      fixes({ section: { heading: "Suggested fix not available", sentence: "A reason." } }),
    );

    const section = container.querySelector<HTMLElement>(`#${SUGGESTED_FIX.anchor}`);
    expect(section).not.toBeNull();
    expect(section?.querySelector("h2")?.textContent).toBe("Suggested fix not available");
    const evidence = container.querySelector("#finding-evidence");
    expect(
      evidence !== null &&
        section !== null &&
        evidence.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(evidence?.contains(section ?? null)).toBe(false);
    const jump = [...container.querySelectorAll("header a")].find(
      (each) => readable(each) === SUGGESTED_FIX.jump,
    );
    expect(jump?.getAttribute("href")).toBe(`#${SUGGESTED_FIX.anchor}`);
  });

  it("a line with no heading of its own renders its sentence without a heading", async () => {
    const container = await page(fixes({ section: { heading: null, sentence: "One line." } }));

    const section = container.querySelector(`#${SUGGESTED_FIX.anchor}`);
    expect(readable(section as Element)).toBe("One line.");
    expect(section?.querySelector("h2")).toBeNull();
  });
});

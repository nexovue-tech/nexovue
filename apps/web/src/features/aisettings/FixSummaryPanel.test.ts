// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { mount, settle, unmountAll } from "@/shared/router-harness.test-util";

import { FIX_SUMMARY_WORDS, type FixSummary, FixSummaryPanel } from "./FixSummaryPanel";

/**
 * The admin summary of suggested fixes (F5 plan T9.3; spec UR12, AC39; design, *The summary*).
 * Each form is stated: loading, failed, empty, no permission. A count of 0 reads 0, an unknown
 * one an em dash. A notice names the rule and the files, never the suggestion's text.
 */

const API_PREFIX = "/api/v1";
const EMPTY = "No suggested fixes asked for in the last 30 days.";
const K7 =
  "Rule K7 withheld a suggestion because it added hidden or look-alike characters. A planted instruction in a file of this repository may look like that.";

const NOTHING: FixSummary = {
  days: 30,
  empty: EMPTY,
  requested: 0,
  refused: [],
  verdicts: [
    { verdict: "merge_as_is", label: "Yes, nearly as it is", count: 0 },
    { verdict: "useful_start", label: "Yes, as a start", count: 0 },
    { verdict: "wrong", label: "No, it was wrong", count: 0 },
  ],
  drafts_opened: 0,
  drafts_merged: 0,
  tokens: 0,
  notices: [],
};

const SOME: FixSummary = {
  ...NOTHING,
  requested: 4,
  refused: [
    { reason: "withheld_K7", label: "Rule K7: added hidden or look-alike characters", count: 1 },
  ],
  verdicts: [
    { verdict: "merge_as_is", label: "Yes, nearly as it is", count: 0 },
    { verdict: "useful_start", label: "Yes, as a start", count: 2 },
    { verdict: "wrong", label: "No, it was wrong", count: 1 },
  ],
  tokens: 12840,
  notices: [
    {
      rule: "K7",
      sentence: K7,
      at: "2026-11-21T10:00:00Z",
      fix_id: "f-1",
      finding_id: "0b9f3a52-6a55-4c1e-9d0e-3c5a7c1e2f10",
      files: ["services/shop/app/handler.py"],
    },
  ],
};

function stub(role: TestRole, answer: FixSummary | number) {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname.slice(API_PREFIX.length);
      asked.push(path);
      if (path === "/auth/me") {
        return new Response(JSON.stringify(identityFor(role)), {
          headers: { "content-type": "application/json" },
        });
      }
      if (path === "/ai/fix-summary" && typeof answer === "number") {
        return new Response(JSON.stringify({ title: "no", status: answer }), {
          status: answer,
          headers: { "content-type": "application/problem+json" },
        });
      }
      return new Response(JSON.stringify(answer), {
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return asked;
}

async function shown(role: TestRole, answer: FixSummary | number) {
  const asked = stub(role, answer);
  const page = await mount(() => createElement(FixSummaryPanel));
  for (let round = 0; round < 6; round += 1) await settle();
  return { page, asked };
}

afterEach(() => {
  unmountAll();
  vi.unstubAllGlobals();
});

describe("the suggested fix summary (UR12, AC39)", () => {
  it("says the design's empty sentence when nothing was asked for", async () => {
    const { page } = await shown("admin", NOTHING);

    expect(page.textContent).toContain(EMPTY);
  });

  it("shows true counts, 0 as 0, every verdict, and tokens", async () => {
    const { page } = await shown("owner", SOME);
    const text = page.textContent ?? "";

    expect(text).toContain(`${FIX_SUMMARY_WORDS.requested}: 4`);
    expect(text).toContain("Rule K7: added hidden or look-alike characters: 1");
    expect(text).toContain("Yes, nearly as it is: 0");
    expect(text).toContain("Yes, as a start: 2");
    expect(text).toContain(`${FIX_SUMMARY_WORDS.draftsOpened}: 0`);
    expect(text).toContain(`${FIX_SUMMARY_WORDS.tokens}: 12,840`);
    expect(text).not.toContain(EMPTY);
  });

  it("draws an em dash for a count the API did not give", async () => {
    const { page } = await shown("owner", {
      ...SOME,
      drafts_merged: null as unknown as number,
    });

    expect(page.textContent).toContain(`${FIX_SUMMARY_WORDS.draftsMerged}: —`);
  });

  it("a notice names the rule, the files and the finding, never the suggestion's text", async () => {
    const { page } = await shown("admin", SOME);
    const notices = page.querySelector('[aria-label="Notices"]');

    expect(notices?.textContent).toContain(K7);
    expect(notices?.textContent).toContain("services/shop/app/handler.py");
    const link = notices?.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/findings/0b9f3a52-6a55-4c1e-9d0e-3c5a7c1e2f10");
  });

  it("a support view's notice, with no paths, says the files are not shown", async () => {
    const { page } = await shown("admin", {
      ...SOME,
      notices: SOME.notices.map((notice) => ({ ...notice, files: null })),
    });

    expect(page.textContent).toContain(FIX_SUMMARY_WORDS.filesHidden);
    expect(page.textContent).not.toContain("handler.py");
  });

  it("a member reads the no-permission sentence and nothing is asked", async () => {
    const { page, asked } = await shown("member", SOME);

    expect(page.textContent).toContain(FIX_SUMMARY_WORDS.noPermission);
    expect(asked).not.toContain("/ai/fix-summary");
  });

  it("a refused read says the same, in place", async () => {
    const { page } = await shown("admin", 403);

    expect(page.textContent).toContain(FIX_SUMMARY_WORDS.noPermission);
  });

  it("a failed read says so, with a retry, and never reads as empty", async () => {
    const { page } = await shown("admin", 500);

    expect(page.textContent).toContain(FIX_SUMMARY_WORDS.failed);
    expect(page.textContent).not.toContain(EMPTY);
  });
});

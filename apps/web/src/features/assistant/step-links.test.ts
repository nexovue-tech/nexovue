import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { message, PROJECT_ID, step } from "./panel.test-util";
import { STARTERS, starterPage, startersFor } from "./starters";
import { KEYWORD_ONLY_STEP, NO_ROWS, READ_REFUSED_FOR_NOW } from "./states";
import { stepHref, stepOutcome, stepsAsText, stepTarget } from "./step-links";

/**
 * F4 T3.3 and T3.4: where a step leads and what it says (AC22, spec *Error cases*), Copy
 * steps holding no model word (AC93), and the starters by page (AC49). The catalog's own
 * destinations are held to `STEP_TARGETS` by `test_assistant_catalog.py`.
 */

const NOW = Date.parse("2026-11-12T10:00:00Z");
const ORIGIN = "https://console.example";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("where a step leads", () => {
  it("fills a route's parameters and carries the project and range in the address", () => {
    const link = {
      path: "/services/$name",
      params: { name: "check out" },
      query: { project: PROJECT_ID, from: "2026-11-12T09:00:00Z", to: "2026-11-12T09:30:00Z" },
      caveat: null,
    };
    expect(stepTarget(link)).toBe("/services/$name");
    expect(stepHref(link, ORIGIN)).toBe(
      `${ORIGIN}/services/check%20out?project=${PROJECT_ID}&from=2026-11-12T09%3A00%3A00Z&to=2026-11-12T09%3A30%3A00Z`,
    );
  });

  it("offers no link for a path it does not know, or one missing its parameter", () => {
    expect(stepTarget({ path: "/llm", params: {}, query: {}, caveat: null })).toBeNull();
    expect(
      stepTarget({ path: "/traces/$traceId", params: {}, query: {}, caveat: null }),
    ).toBeNull();
  });
});

describe("what a step says it read", () => {
  it("says an empty read is no matching rows in what was stored", () => {
    expect(stepOutcome(step({ row_count: 0 }))).toBe(`${NO_ROWS}.`);
  });

  it("says an empty knowledge search is no record of what is kept, with its days", () => {
    // FR42, word for word: absence must not read as "it never happened".
    expect(stepOutcome(step({ tool: "search_knowledge", row_count: 0, cases_kept_days: 30 }))).toBe(
      "No record of this in what is kept. Cases are kept for 30 days after they end.",
    );
    expect(stepOutcome(step({ tool: "search_knowledge", row_count: 2, from_time: null }))).toBe(
      "Latest 2, no time filter.",
    );
  });

  it("says a knowledge step searched by keyword only, and says nothing more for hybrid", () => {
    // FR27, AC41: each answer using keyword search alone says so.
    expect(
      stepOutcome(
        step({ tool: "search_knowledge", row_count: 2, from_time: null, search: "keyword" }),
      ),
    ).toBe(`Latest 2, no time filter. ${KEYWORD_ONLY_STEP}`);
    expect(
      stepOutcome(
        step({ tool: "search_knowledge", row_count: 0, cases_kept_days: 30, search: "keyword" }),
      ),
    ).toBe(
      `No record of this in what is kept. Cases are kept for 30 days after they end. ${KEYWORD_ONLY_STEP}`,
    );
    expect(
      stepOutcome(
        step({ tool: "search_knowledge", row_count: 2, from_time: null, search: "hybrid" }),
      ),
    ).toBe("Latest 2, no time filter.");
    // Only the knowledge tool searches; another step never says it.
    expect(stepOutcome(step({ row_count: 2, search: "keyword" }))).not.toContain(KEYWORD_ONLY_STEP);
  });

  it("links a knowledge step to the runbooks list, carrying no phrase", () => {
    const link = {
      path: "/settings/runbooks",
      params: {},
      query: { project: PROJECT_ID },
      caveat: "The search phrase is not carried.",
    };
    expect(stepTarget(link)).toBe("/settings/runbooks");
    expect(stepHref(link, ORIGIN)).toBe(`${ORIGIN}/settings/runbooks?project=${PROJECT_ID}`);
  });

  it("says a windowless read is the latest rows, with no time filter", () => {
    expect(stepOutcome(step({ from_time: null, to_time: null, row_count: 12 }))).toBe(
      "Latest 12, no time filter.",
    );
  });

  it("says an aged-out read in code's words, with the first day still kept", () => {
    expect(
      stepOutcome(step({ state: "aged_out", row_count: null, kept_since: "2026-11-05" })),
    ).toBe("The data before 2026-11-05 is no longer kept.");
    expect(stepOutcome(step({ row_count: 4, kept_since: "2026-11-05" }))).toBe(
      "4 rows read. The data before 2026-11-05 is no longer kept.",
    );
  });

  it("says a read the rate limit refused was refused for now", () => {
    expect(stepOutcome(step({ state: "failed", failure: "limited", row_count: null }))).toBe(
      READ_REFUSED_FOR_NOW,
    );
  });
});

describe("Copy steps (AC93)", () => {
  it("holds nexovue's words and links, and no model word", () => {
    const marker = "MODEL-WORD-7f3a";
    const shown = message({
      question: `Why? ${marker}`,
      answer: {
        sentences: [
          { text: `It failed ${marker}.`, label: "unchecked", cause_warning: false, cites: ["s1"] },
        ],
        removed_sentence_count: 0,
        not_verified: [marker],
      },
      steps: [
        step({ display_arguments: marker }),
        step({
          key: "s2",
          position: 2,
          tool: "search_logs",
          words: "Reading logs",
          link: { path: "/logs", params: {}, query: { project: PROJECT_ID }, caveat: null },
        }),
      ],
    });

    const copied = stepsAsText(shown.steps, { zone: "UTC", now: NOW, origin: ORIGIN });

    expect(copied).not.toContain(marker);
    expect(copied).toContain("s1. Reading services");
    expect(copied).toContain(`${ORIGIN}/logs?project=${PROJECT_ID}`);
    expect(copied).toContain("The range is not applied here.");
  });
});

describe("starters by page (AC49)", () => {
  it.each([
    [
      { headline: "checkout is slower", findingId: "f1", serviceName: "checkout" },
      "/findings/f1",
      "finding",
    ],
    [null, "/services/checkout", "service"],
    [{ headline: "checkout", serviceName: "checkout" }, "/services/checkout", "service"],
    [null, "/traces", "telemetry"],
    [null, "/logs", "telemetry"],
    [null, "/errors", "telemetry"],
    [null, "/", "elsewhere"],
    [null, "/settings/ai", "elsewhere"],
  ] as const)("on %s at %s, offers the %s row", (subject, path, page) => {
    expect(starterPage(subject, path)).toBe(page);
    expect(startersFor(subject, path)).toBe(STARTERS[page]);
  });

  it("matches the design's table word for word", () => {
    expect(STARTERS).toEqual({
      finding: [
        "What changed just before this started?",
        "What do the logs say in this window?",
        "Has this happened before?",
      ],
      service: [
        "What is this service's error rate this hour?",
        "Which endpoint moved most?",
        "What does it depend on?",
      ],
      telemetry: [
        "What are the commonest failures in this window?",
        "Which service do most of these come from?",
        "What should I look at next?",
      ],
      elsewhere: [
        "What is failing right now?",
        "What deployed today?",
        "How did we fix this last time?",
      ],
    });
  });
});

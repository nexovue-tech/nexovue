import { describe, expect, it } from "vitest";

import type { DashboardContext, PanelReadResponse, PanelResult } from "./api";
import type { PageLineInput } from "./page-lines";
import { pageLines, staleAfterMs, widerRange } from "./page-lines";

/**
 * The page line's priority table, one test per row (F20, FR48, T6.6; AC70, AC72, AC105,
 * AC109, AC140). Every instant is fixed: no wall clock is read.
 */

const NOW = Date.parse("2026-11-11T10:00:00.000Z");
const MIN = 60_000;
const at = (minutesAgo: number) => new Date(NOW - minutesAgo * MIN).toISOString();

const ok = (endsMinutesAgo = 1): PanelResult => ({
  state: "ok",
  kind: "service_trend",
  read_at: at(0),
  bucket_seconds: 60,
  data_starts_at: at(30),
  data_ends_at: at(endsMinutesAgo),
  series: [],
});

const answer = (panels: Record<string, PanelResult>): PanelReadResponse => ({
  correlation_id: "corr-1",
  window_start: at(60),
  window_end: at(0),
  panels,
});

const context = (over: Partial<DashboardContext> = {}): DashboardContext =>
  ({ ever_reported: true, last_seen_at: at(180), ...over }) as DashboardContext;

function input(over: Partial<PageLineInput> = {}): PageLineInput {
  return {
    now: NOW,
    zone: "UTC",
    canSetUp: true,
    refreshing: true,
    intervalMs: 60_000,
    readingSince: undefined,
    answer: answer({ a: ok() }),
    scanCount: 1,
    lastGoodAt: at(0),
    awaitingRead: false,
    projectName: "staging",
    pickerUnset: false,
    ...over,
  };
}

describe("the page's severe line: one at a time, in order", () => {
  it("says nothing when reads succeed and the data is current", () => {
    expect(pageLines(input())).toEqual({ severe: undefined, state: undefined, context: [] });
  });

  it("becomes NOT CURRENT after three missed intervals, and is then the only severe line", () => {
    const lines = pageLines(
      input({
        lastGoodAt: at(4),
        failure: { kind: "failed", correlationId: "c" },
        detectionTitle: "Learning your services' normal.",
      }),
    );
    expect(lines.severe?.kind).toBe("not-current");
    expect(lines.severe?.words).toBe("NOT CURRENT. Last good reading 09:56 UTC");
  });

  it("caps three intervals at five minutes, so a 5-minute refresh does not hide a failure for 15", () => {
    expect(staleAfterMs(60_000)).toBe(180_000);
    expect(staleAfterMs(300_000)).toBe(300_000);
  });

  it.each([
    ["paused or fixed or off", { refreshing: false }],
    ["a tab just back from hidden, before its first read", { awaitingRead: true }],
  ])("never fires the banner for %s", (_name, over) => {
    const lines = pageLines(input({ lastGoodAt: at(30), failure: { kind: "failed" }, ...over }));
    expect(lines.severe?.kind).toBe("outage");
  });

  it("says the outage once, with the reference, while a recent good reading still stands", () => {
    const lines = pageLines(
      input({ lastGoodAt: at(1), failure: { kind: "failed", correlationId: "c9" } }),
    );
    expect(lines.severe).toMatchObject({
      kind: "outage",
      words: "Could not read telemetry since 09:59 UTC. Notes and findings below are current.",
      correlationId: "c9",
    });
  });

  it("reads every scan panel failing as the outage too", () => {
    const failed: PanelResult = { state: "failed", kind: "service_trend" };
    expect(
      pageLines(input({ answer: answer({ a: failed, b: failed }), scanCount: 2 })).severe?.kind,
    ).toBe("outage");
  });

  it("gives the 25 second state its own words (AC111)", () => {
    expect(
      pageLines(input({ failure: { kind: "timed-out", correlationId: "c" } })).severe?.words,
    ).toBe("The page did not answer within 25 seconds.");
  });

  it("says FR45's sentence for a whole-request 429, offering no second read while one is waiting (AC145)", () => {
    const waiting = pageLines(input({ failure: { kind: "refused", retryScheduled: true } })).severe;
    expect(waiting?.words).toBe("Too many reads just now.");
    expect(waiting?.action).toBeUndefined();
    const spent = pageLines(input({ failure: { kind: "refused", retryScheduled: false } })).severe;
    expect(spent?.action?.id).toBe("read-again");
  });

  it("says the pipeline may be behind when reads succeed and the newest data is over ten minutes old (AC70)", () => {
    const lines = pageLines(input({ answer: answer({ a: ok(30) }) }));
    expect(lines.severe).toMatchObject({
      kind: "stale-telemetry",
      words:
        "Newest telemetry here is 29 minutes old. The pipeline may be behind, or nothing is sending",
    });
  });

  it("does not say it on a page that is not live", () => {
    expect(
      pageLines(input({ refreshing: false, answer: answer({ a: ok(30) }) })).severe,
    ).toBeUndefined();
  });

  it("puts detection's line below the pipeline's, and shows it alone when that is current", () => {
    expect(pageLines(input({ detectionTitle: "Detection is not running." })).severe).toEqual({
      kind: "detection",
      words: "Detection is not running.",
    });
  });

  it("says 'Still reading' only past three seconds of a first read (AC72)", () => {
    expect(
      pageLines(input({ answer: undefined, readingSince: NOW - 2_900 })).severe,
    ).toBeUndefined();
    expect(pageLines(input({ answer: undefined, readingSince: NOW - 3_000 })).severe?.words).toBe(
      "Still reading. This can take up to 25 seconds",
    );
  });
});

describe("the page's state line", () => {
  it("holds the picker's place when it is unset, and no panel is read for it (AC140)", () => {
    expect(pageLines(input({ pickerUnset: true })).state).toEqual({
      kind: "picker",
      words: "Choose a service to fill this dashboard",
    });
  });

  it("shows a link's unknown service quoted, truncated and marked as the link's (AC123)", () => {
    const long = `${"x".repeat(60)}\u202Eevil`;
    const line = pageLines(input({ unknownService: long })).state;
    expect(line?.kind).toBe("unknown-service");
    expect(line?.words).toBe(`“${"x".repeat(40)}…” from this link was not found in staging.`);
    expect(pageLines(input({ unknownService: "a\u202Eb" })).state?.words).toContain("[U+202E]");
  });

  it("says a project that never reported has not, with setup for those who can act (AC109, AC105)", () => {
    const editor = pageLines(input({ context: context({ ever_reported: false }) })).state;
    expect(editor).toMatchObject({ words: "Nothing has reported to staging yet.", setup: true });
    const viewer = pageLines(
      input({ canSetUp: false, context: context({ ever_reported: false }) }),
    ).state;
    expect(viewer?.words).toBe(
      "Nobody has connected staging yet. An owner or admin can set it up.",
    );
    expect(viewer?.setup).toBeUndefined();
  });

  it("says a quiet window says when it last saw anything, and offers the next range (AC109)", () => {
    const empty: PanelResult = {
      state: "empty",
      kind: "service_trend",
      read_at: at(0),
      last_seen_at: null,
    };
    const lines = pageLines(
      input({ context: context(), answer: answer({ a: empty }), wider: widerRange("1h") }),
    );
    expect(lines.state).toEqual({
      kind: "quiet",
      words: "Nothing reported in this window. Last seen 3 hr. ago.",
      action: { id: "show-wider", label: "Show the last 6 hours" },
    });
  });
});

describe("the context line merges what the address says (FR53)", () => {
  it("names a link's project and the way back", () => {
    const lines = pageLines(input({ fromLink: { own: "production" } }));
    expect(lines.context).toEqual([
      {
        kind: "link",
        words: "Showing staging from this link. You were on production.",
        action: { id: "back-to-project", label: "Back to production" },
      },
    ]);
  });

  it("says a fixed window by its times, and warns when its newest minutes can still change (AC28)", () => {
    const settled = pageLines(input({ fixedTimes: "02:00 to 03:00 UTC" })).context[0];
    expect(settled?.words).toBe("Fixed time: 02:00 to 03:00 UTC.");
    expect(settled?.action.label).toBe("Back to live");
    const recent = pageLines(input({ fixedTimes: "09:00 to 10:00 UTC", endsRecently: true }))
      .context[0];
    expect(recent?.words).toBe(
      "Fixed time: 09:00 to 10:00 UTC. Its newest minutes can still change.",
    );
  });

  it("offers the way back from a zoom, and says it once beside a link", () => {
    const lines = pageLines(
      input({
        fromLink: { own: undefined },
        fixedTimes: "02:10 to 02:20 UTC",
        zoomed: { before: "02:00 to 03:00 UTC" },
      }),
    );
    expect(lines.context.map((segment) => segment.kind)).toEqual(["link", "zoomed"]);
    expect(lines.context[1]?.action.label).toBe("Back to 02:00 to 03:00 UTC");
    expect(lines.context[1]?.words).toBe("Zoomed to 02:10 to 02:20 UTC.");
  });
});

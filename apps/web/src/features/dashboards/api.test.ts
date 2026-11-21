// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api-client";

import type { PanelReadResponse, PanelResult } from "./api";
import { effectiveIntervalMs, nextReadAfterMs, WHOLE_REQUEST_RETRIES } from "./api";
import type { PanelCatchUp } from "./catch-up";
import { CATCH_UP_TRIES, mergeCatchUp, refusedPanels, usePanelCatchUp } from "./catch-up";

/**
 * How a dashboard reads (F20, T6.4): its own interval, the tab's choice, the wait a whole
 * request's 429 asks for (AC145), and a refused panel reading again by itself (FR45,
 * AC71). Fake dates only where a clock is read; the catch-up's timers are advanced by hand.
 */

describe("what the page refreshes at (AC29, AC104, AC118)", () => {
  const base = {
    saved: 60_000 as number | false,
    tab: undefined,
    fixedWindow: false,
    paused: false,
  };

  it("follows the dashboard's own setting", () => {
    expect(effectiveIntervalMs(base)).toBe(60_000);
    expect(effectiveIntervalMs({ ...base, saved: false })).toBe(false);
  });

  it("lets this tab choose its own, within the 30 second floor", () => {
    expect(effectiveIntervalMs({ ...base, tab: 300_000 })).toBe(300_000);
    expect(effectiveIntervalMs({ ...base, tab: 5_000 })).toBe(30_000);
  });

  it("never refreshes a fixed window or a paused page, whatever a tab chose", () => {
    expect(effectiveIntervalMs({ ...base, tab: 30_000, fixedWindow: true })).toBe(false);
    expect(effectiveIntervalMs({ ...base, tab: 30_000, paused: true })).toBe(false);
  });
});

function refusal(retryAfter?: number): ApiError {
  return new ApiError("Too many requests", 429, {
    traceId: "t",
    correlationId: "c",
    retryAfterSeconds: retryAfter,
    offline: false,
    aborted: false,
  });
}

describe("when the page reads next (AC145)", () => {
  it("waits at least the server's Retry-After after a whole-request 429, even with refresh off", () => {
    expect(
      nextReadAfterMs({ intervalMs: false, paused: false, error: refusal(20), refusals: 1 }),
    ).toBe(20_000);
    expect(
      nextReadAfterMs({ intervalMs: 5_000, paused: false, error: refusal(20), refusals: 1 }),
    ).toBe(20_000);
    expect(
      nextReadAfterMs({ intervalMs: 60_000, paused: false, error: refusal(20), refusals: 1 }),
    ).toBe(60_000);
  });

  it("stops asking after the tries are spent, and when paused", () => {
    expect(
      nextReadAfterMs({
        intervalMs: false,
        paused: false,
        error: refusal(20),
        refusals: WHOLE_REQUEST_RETRIES + 1,
      }),
    ).toBe(false);
    expect(
      nextReadAfterMs({ intervalMs: false, paused: true, error: refusal(20), refusals: 1 }),
    ).toBe(false);
  });

  it("is the page's own interval after anything else", () => {
    expect(nextReadAfterMs({ intervalMs: 60_000, paused: false, error: null, refusals: 0 })).toBe(
      60_000,
    );
  });
});

const refused = (seconds: number): PanelResult => ({
  state: "refused",
  kind: "service_trend",
  retry_after_seconds: seconds,
});
const okPanel: PanelResult = {
  state: "ok",
  kind: "service_trend",
  read_at: "2026-11-11T10:00:00.000Z",
  bucket_seconds: 60,
  data_starts_at: null,
  data_ends_at: null,
  series: [],
};
const answer = (panels: Record<string, PanelResult>): PanelReadResponse => ({
  correlation_id: "first",
  window_start: "2026-11-11T09:00:00.000Z",
  window_end: "2026-11-11T10:00:00.000Z",
  panels,
});

describe("the panels to read again", () => {
  it("lists only the refused, and the longest wait the server gave", () => {
    expect(refusedPanels(answer({ a: refused(20), b: okPanel, c: refused(35) }))).toEqual({
      ids: ["a", "c"],
      waitSeconds: 35,
    });
  });

  it("lays what was read again over the answer, quoting the later read's reference", () => {
    const merged = mergeCatchUp(answer({ a: refused(20), b: okPanel }), { a: okPanel }, "second");
    expect(merged.panels.a?.state).toBe("ok");
    expect(merged.correlation_id).toBe("second");
  });
});

let root: Root | undefined;
let seen: PanelCatchUp | undefined;
const SCOPE = {
  dashboardId: "d",
  projectId: "p",
  choice: {
    kind: "absolute",
    from: "2026-11-11T09:00:00.000Z",
    to: "2026-11-11T10:00:00.000Z",
  } as const,
};

function Probe(props: { base: PanelReadResponse; paused: boolean }) {
  seen = usePanelCatchUp({ base: props.base, scope: SCOPE, paused: props.paused });
  return null;
}

async function mount(base: PanelReadResponse, paused = false) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  root = createRoot(container);
  await act(async () => root?.render(createElement(Probe, { base, paused })));
  return (next: boolean) =>
    act(async () => root?.render(createElement(Probe, { base, paused: next })));
}

afterEach(() => {
  if (root) act(() => root?.unmount());
  root = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function stubReads(answers: PanelReadResponse[]) {
  const urls: string[] = [];
  let index = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      urls.push(url.search);
      const next = answers[Math.min(index, answers.length - 1)];
      index += 1;
      return new Response(JSON.stringify(next), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return urls;
}

describe("a refused panel reads again by itself (FR45, AC71)", () => {
  it("reads only the refused panels after the server's wait, and fills them in", async () => {
    vi.useFakeTimers();
    const urls = stubReads([answer({ a: okPanel })]);
    await mount(answer({ a: refused(20), b: okPanel }));
    expect(seen?.readingAgainIn).toBe(20);
    expect(urls).toHaveLength(0);

    await act(async () => vi.advanceTimersByTimeAsync(20_000));
    expect(urls).toHaveLength(1);
    expect(new URLSearchParams(urls[0]).get("panels")).toBe("a");
    expect(seen?.answer?.panels.a?.state).toBe("ok");
    expect(seen?.readingAgainIn).toBeUndefined();
  });

  it("stops after three tries, and offers no countdown", async () => {
    vi.useFakeTimers();
    const urls = stubReads([answer({ a: refused(5) })]);
    await mount(answer({ a: refused(5) }));
    for (let round = 0; round < 5; round += 1) {
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
    }
    expect(urls).toHaveLength(3);
    expect(CATCH_UP_TRIES).toBe(3);
    expect(seen?.readingAgainIn).toBeUndefined();
    expect(seen?.answer?.panels.a?.state).toBe("refused");
  });

  it("waits while paused, and reads when resumed", async () => {
    vi.useFakeTimers();
    const urls = stubReads([answer({ a: okPanel })]);
    const setPaused = await mount(answer({ a: refused(5) }), true);
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(urls).toHaveLength(0);
    expect(seen?.readingAgainIn).toBeUndefined();

    await setPaused(false);
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(urls).toHaveLength(1);
  });

  it("reads one panel at once on its own 'Read again', spending no try", async () => {
    vi.useFakeTimers();
    const urls = stubReads([answer({ a: okPanel })]);
    await mount(answer({ a: { state: "failed", kind: "service_trend" } }));
    await act(async () => seen?.readAgain(["a"]));
    expect(new URLSearchParams(urls[0]).get("panels")).toBe("a");
    expect(seen?.answer?.panels.a?.state).toBe("ok");
  });
});

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetDiagnostics } from "./diagnostics";
import { observeLifecycle } from "./lifecycle";

let exits: number;
let intervals: number;
let stop: () => void;

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
}

beforeEach(() => {
  resetDiagnostics();
  vi.useFakeTimers();
  exits = 0;
  intervals = 0;
  setVisibility("visible");
  stop = observeLifecycle({
    onExit: () => {
      exits++;
    },
    onInterval: () => {
      intervals++;
    },
    intervalMs: 10_000,
  });
});

afterEach(() => {
  stop();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("when the page goes away", () => {
  it("flushes when the page becomes hidden", () => {
    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(exits).toBe(1);
  });

  it("does not flush when the page becomes visible again", () => {
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(exits).toBe(0);
  });

  it("flushes on pagehide, for the Safari paths where visibilitychange does not fire", () => {
    window.dispatchEvent(new Event("pagehide"));
    expect(exits).toBe(1);
  });
});

describe("the periodic flush", () => {
  it("runs while the page is visible", () => {
    vi.advanceTimersByTime(30_000);
    expect(intervals).toBe(3);
  });

  it("does not run in a hidden tab, where browsers throttle timers anyway", () => {
    setVisibility("hidden");
    vi.advanceTimersByTime(30_000);
    expect(intervals).toBe(0);
  });

  it("stops after teardown", () => {
    stop();
    vi.advanceTimersByTime(30_000);
    window.dispatchEvent(new Event("pagehide"));
    expect(intervals).toBe(0);
    expect(exits).toBe(0);
  });
});

describe("the back/forward cache promise", () => {
  it("registers neither unload nor beforeunload, which would cost every page its bfcache", () => {
    stop();
    const onWindow = vi.spyOn(window, "addEventListener");
    const onDocument = vi.spyOn(document, "addEventListener");

    stop = observeLifecycle({
      onExit: () => undefined,
      onInterval: () => undefined,
      intervalMs: 10_000,
    });

    const registered = [...onWindow.mock.calls, ...onDocument.mock.calls].map((call) =>
      String(call[0]),
    );
    expect(registered).not.toContain("unload");
    expect(registered).not.toContain("beforeunload");
    // visibilitychange goes on document, so it runs after web-vitals' own
    // window-capture listener has queued the final CLS and INP values.
    expect(onDocument.mock.calls.map((call) => String(call[0]))).toContain("visibilitychange");
    expect(onWindow.mock.calls.map((call) => String(call[0]))).toContain("pagehide");
  });
});

describe("a handler that throws", () => {
  it("never reaches the page", () => {
    stop();
    stop = observeLifecycle({
      onExit: () => {
        throw new Error("defect in the flush");
      },
      onInterval: () => {
        throw new Error("defect in the tick");
      },
      intervalMs: 10_000,
    });

    expect(() => window.dispatchEvent(new Event("pagehide"))).not.toThrow();
    expect(() => vi.advanceTimersByTime(10_000)).not.toThrow();
  });
});

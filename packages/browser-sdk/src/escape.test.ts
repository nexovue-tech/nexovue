// @vitest-environment jsdom

/**
 * Can an exception from inside the SDK reach the customer's page?
 *
 * An observability script that breaks the site it observes is worse than no
 * observability, so every point the browser calls into has to swallow its own
 * failures. These tests are the adversarial pass: each one drives a real browser
 * entry point with something that throws, and asserts the page never sees it.
 *
 * jsdom reports an exception thrown inside a listener the way a browser does —
 * as an `error` event on window, not by rejecting the dispatch — so the probe
 * below listens for that rather than wrapping the call in a try.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { failureCounts, resetDiagnostics } from "./diagnostics";
import { whenPageLoaded } from "./pageview";
import { reportWebVitals } from "./vitals";

/** Everything that reached the page while `run` executed. */
function escapedToThePage(run: () => void): string[] {
  const escaped: string[] = [];
  const onError = (event: ErrorEvent) => {
    escaped.push(event.message);
    // Stop jsdom's default, which prints the trace and fails the run noisily.
    event.preventDefault();
  };
  window.addEventListener("error", onError);
  try {
    run();
  } finally {
    window.removeEventListener("error", onError);
  }
  return escaped;
}

beforeEach(() => {
  resetDiagnostics();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the load listener", () => {
  it("does not let a throwing callback reach the page", () => {
    // The real callback starts a span, serialises it and enqueues it — three
    // places that can throw on a payload the page produced.
    const cancel = whenPageLoaded(() => {
      throw new Error("span serialisation failed");
    });

    const escaped = escapedToThePage(() => {
      window.dispatchEvent(new Event("load"));
    });

    cancel();
    expect(escaped).toEqual([]);
    expect(failureCounts()["pageview.loaded"]).toBe(1);
  });

  it("still reports the load when the page timing API is missing", () => {
    // performance.getEntriesByType is absent in older engines and throws in a
    // few embedded ones. Losing the timestamp must not lose the page view.
    vi.spyOn(performance, "getEntriesByType").mockImplementation(() => {
      throw new Error("no navigation timing here");
    });

    let loadedAt: number | undefined;
    const cancel = whenPageLoaded((at) => {
      loadedAt = at;
    });

    const escaped = escapedToThePage(() => {
      window.dispatchEvent(new Event("load"));
    });

    cancel();
    expect(escaped).toEqual([]);
    expect(loadedAt).toBeGreaterThan(0);
  });
});

describe("registering the web vitals", () => {
  it("keeps collecting the rest when one metric cannot be observed", async () => {
    // web-vitals reaches PerformanceObserver at registration. An engine missing
    // one entry type must cost that metric, not every metric — and not the error
    // capture or the page view that are registered after it.
    //
    // The module is mocked rather than spied: an ESM namespace is frozen, so
    // vi.spyOn cannot redefine an export.
    vi.doMock("web-vitals", () => ({
      onCLS: (report: unknown) => void report,
      onFCP: (report: unknown) => void report,
      onINP: () => {
        throw new TypeError("PerformanceObserver is not defined");
      },
      onLCP: (report: unknown) => void report,
      onTTFB: (report: unknown) => void report,
    }));
    vi.resetModules();

    const { reportWebVitals: subscribe } = await import("./vitals");
    const { failureCounts: counts, resetDiagnostics: reset } = await import("./diagnostics");
    reset();

    expect(() => subscribe(() => undefined)).not.toThrow();

    // Exactly the one that threw is counted; the other four subscribed.
    expect(counts()["vitals.register"]).toBe(1);
    vi.doUnmock("web-vitals");
    vi.resetModules();
  });

  it("does not let a throwing consumer reach the browser's callback", () => {
    // The consumer is what enqueues the record, and enqueueing serialises it.
    expect(() =>
      reportWebVitals(() => {
        throw new Error("enqueue failed");
      }),
    ).not.toThrow();
    expect(failureCounts()["vitals.register"]).toBeUndefined();
  });
});

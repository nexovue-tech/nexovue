import { guard } from "./diagnostics";

/**
 * When the page finished loading, in epoch milliseconds, and how to wait for it.
 *
 * A page view is a span, which means the tail sampler judges it: it keeps every
 * errored trace, every trace slower than the project's threshold, and a
 * percentage of healthy ones. A fast page load is a healthy one-span trace, so
 * most individual page views will not be retrievable — the per-minute COUNT
 * survives, because it is derived before the sampler runs.
 */

/**
 * Runs once the document has finished loading, or immediately if it already has.
 *
 * Guarded, because what it runs is not small: the callback starts a span,
 * serialises it and enqueues it, and it is invoked from the page's own `load`
 * event. An exception there is an exception in the customer's load handler.
 */
export function whenPageLoaded(run: (loadedAtEpochMillis: number) => void): () => void {
  const report = guard("pageview.loaded", () => run(pageLoadEndedAt()));
  if (document.readyState === "complete") {
    report();
    return () => undefined;
  }
  window.addEventListener("load", report, { once: true });
  return () => window.removeEventListener("load", report);
}

/** performance.timeOrigin is navigation start, which is where a page view begins. */
export function navigationStartedAt(): number {
  return performance.timeOrigin;
}

/**
 * loadEventEnd is only filled in after the load event handler returns, so during
 * the event itself the wall clock is the closest reading there is.
 *
 * The navigation entry is read defensively: the Navigation Timing API is absent
 * in older engines and throws in a few embedded ones, and losing the precise
 * timestamp must not cost the page view itself.
 */
function pageLoadEndedAt(): number {
  try {
    const [entry] = performance.getEntriesByType("navigation");
    const loadEventEnd = (entry as PerformanceNavigationTiming | undefined)?.loadEventEnd ?? 0;
    if (loadEventEnd > 0) return performance.timeOrigin + loadEventEnd;
  } catch {
    // No navigation timing. The wall clock below still dates the page view.
  }
  return Date.now();
}

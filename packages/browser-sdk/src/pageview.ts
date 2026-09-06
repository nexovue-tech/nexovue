/**
 * When the page finished loading, in epoch milliseconds, and how to wait for it.
 *
 * A page view is a span, which means the tail sampler judges it: it keeps every
 * errored trace, every trace slower than the project's threshold, and a
 * percentage of healthy ones. A fast page load is a healthy one-span trace, so
 * most individual page views will not be retrievable — the per-minute COUNT
 * survives, because it is derived before the sampler runs.
 */

/** Runs once the document has finished loading, or immediately if it already has. */
export function whenPageLoaded(run: (loadedAtEpochMillis: number) => void): () => void {
  if (document.readyState === "complete") {
    run(pageLoadEndedAt());
    return () => undefined;
  }
  const onLoad = () => run(pageLoadEndedAt());
  window.addEventListener("load", onLoad, { once: true });
  return () => window.removeEventListener("load", onLoad);
}

/** performance.timeOrigin is navigation start, which is where a page view begins. */
export function navigationStartedAt(): number {
  return performance.timeOrigin;
}

/**
 * loadEventEnd is only filled in after the load event handler returns, so during
 * the event itself the wall clock is the closest reading there is.
 */
function pageLoadEndedAt(): number {
  const [entry] = performance.getEntriesByType("navigation");
  const loadEventEnd = (entry as PerformanceNavigationTiming | undefined)?.loadEventEnd ?? 0;
  return loadEventEnd > 0 ? performance.timeOrigin + loadEventEnd : Date.now();
}

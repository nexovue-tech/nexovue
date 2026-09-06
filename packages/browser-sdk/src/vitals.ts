import type { Metric } from "web-vitals";
import { onCLS, onFCP, onINP, onLCP, onTTFB } from "web-vitals";

import { guard } from "./diagnostics";

/**
 * All five Core Web Vitals from Google's own library, standard build.
 *
 * reportAllChanges stays at its default. With it off the reported delta is still
 * relative to the previously reported value, so summing deltas per metric id
 * gives the same final number — it would only multiply the record count against
 * the browser door's byte allowance.
 *
 * A callback fires more than once per metric per page by design: CLS and INP are
 * not final until the page is hidden, and LCP finalises at the first trusted
 * keydown, click or visibilitychange. Taking only the first would systematically
 * under-report the two metrics Google weights most.
 *
 * onCLS never calls back at all on Firefox or Safari — layout-shift measurement
 * is Chromium-only. That is silence, not a zero, and nothing here invents one.
 */
export function reportWebVitals(emit: (metric: Metric) => void): void {
  const report = guard("vitals.report", emit);
  // Registered one at a time. web-vitals reaches PerformanceObserver as it
  // subscribes, so an engine missing one entry type throws here — and a single
  // throw across all five would cost the other four metrics, the error capture
  // registered after this, and the page view with it.
  //
  // Written out rather than looped: each subscriber is typed to its own metric,
  // so an array of them has no common signature to call.
  const subscribe = guard("vitals.register", (register: () => void) => register());
  subscribe(() => onCLS(report));
  subscribe(() => onFCP(report));
  subscribe(() => onINP(report));
  subscribe(() => onLCP(report));
  subscribe(() => onTTFB(report));
}

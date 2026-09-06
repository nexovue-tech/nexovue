import { guard } from "./diagnostics";

/**
 * When telemetry leaves the page.
 *
 * The flush listener goes on `document`, not on `window` with capture, and that
 * is load-bearing rather than stylistic. web-vitals attaches its own
 * visibilitychange listener on window in the capture phase; the capture path
 * runs window before the document target, so a document listener is guaranteed
 * to run after web-vitals has queued its final CLS and INP values — whichever
 * order the two were registered in. Getting it wrong would silently report CLS
 * numbers lower than the browser's own.
 *
 * `unload` and `beforeunload` are never registered. Either one makes a desktop
 * Chrome or Firefox page ineligible for the back/forward cache — the browser
 * keeping a whole page in memory so Back restores it instantly — and `unload`
 * does not even fire on the common mobile tab-close paths. `pagehide` costs
 * nothing and fires everywhere `unload` does.
 */
export interface LifecycleHandlers {
  /** The page is going away or being backgrounded. Last chance to send. */
  onExit: () => void;
  /** The periodic flush, while the page is visible. */
  onInterval: () => void;
  intervalMs: number;
}

export function observeLifecycle(handlers: LifecycleHandlers): () => void {
  const exit = guard("lifecycle.exit", handlers.onExit);

  const onVisibilityChange = () => {
    if (document.visibilityState === "hidden") exit();
  };
  const onPageHide = () => exit();
  // Browsers throttle timers in a background tab, so a hidden-tab timer is not a
  // delivery mechanism; the exit flush above is what covers a backgrounded page.
  const onTick = guard("lifecycle.tick", () => {
    if (document.visibilityState === "visible") handlers.onInterval();
  });

  // Hidden is the last reliably observable moment, especially on mobile. pagehide
  // is the secondary trigger, for the Safari paths where the first does not fire;
  // both call one idempotent flush, so the usual case of both firing sends once.
  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("pagehide", onPageHide);
  const timer = setInterval(onTick, handlers.intervalMs);

  return () => {
    document.removeEventListener("visibilitychange", onVisibilityChange);
    window.removeEventListener("pagehide", onPageHide);
    clearInterval(timer);
  };
}

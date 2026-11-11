import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Which panels are on screen, so a drag reads only those and the rest as they scroll in
 * (F20, FR53, T4.6): a drag names a window nobody shares, and a 40-scan board read whole
 * on every drag would spend the quota in two drags a minute (design, *Reads in an
 * incident*).
 *
 * Without `IntersectionObserver` (an old browser, or a test) every panel counts as on
 * screen, which reads everything, as before.
 */

const PANEL_ID_ATTRIBUTE = "data-panel-id";

export function useVisiblePanels(): {
  /** A ref for each panel's frame. */
  watch: (element: HTMLElement | null) => void;
  /** Whether a panel is on screen now; true for all when it cannot be known. */
  isVisible: (panelId: string) => boolean;
  /** The panels on screen now. */
  visibleNow: () => ReadonlySet<string> | undefined;
  /** Bumped when the set changes, so a reader can follow it. */
  version: number;
} {
  const visible = useRef(new Set<string>());
  const [version, setVersion] = useState(0);
  const observer = useRef<IntersectionObserver | undefined>(undefined);
  const supported = typeof IntersectionObserver !== "undefined";

  if (supported && observer.current === undefined) {
    observer.current = new IntersectionObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const id = entry.target.getAttribute(PANEL_ID_ATTRIBUTE);
        if (id === null) continue;
        const was = visible.current.has(id);
        if (entry.isIntersecting && !was) visible.current.add(id);
        else if (!entry.isIntersecting && was) visible.current.delete(id);
        else continue;
        changed = true;
      }
      if (changed) setVersion((before) => before + 1);
    });
  }
  useEffect(() => () => observer.current?.disconnect(), []);

  const watch = useCallback((element: HTMLElement | null) => {
    if (element !== null) observer.current?.observe(element);
  }, []);
  const isVisible = useCallback(
    (panelId: string) => !supported || visible.current.has(panelId),
    [supported],
  );
  const visibleNow = useCallback(
    () => (supported ? new Set(visible.current) : undefined),
    [supported],
  );
  return { watch, isVisible, visibleNow, version };
}

export { PANEL_ID_ATTRIBUTE };

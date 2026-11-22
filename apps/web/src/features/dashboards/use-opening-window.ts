import { useEffect, useState } from "react";

import { clearScreenOverride, setScreenOverride } from "@/shared/selection";

import { addressWindow } from "./dashboard-search";

/**
 * The builder opens on the window the dashboard opens on, as the view does (FR52): the top
 * bar and the preview show the dashboard's `default_range` while the builder is on screen,
 * and the reader's remembered window is never written. A range the dashboard does not name
 * leaves the reader's own. A top-bar choice made on purpose ends the override, as anywhere.
 *
 * Returns whether the window is settled, so nothing is read in the reader's own window first
 * and again in the dashboard's.
 */
export function useOpeningWindow(defaultRange: string | null | undefined): boolean {
  const [settled, setSettled] = useState(false);
  // Only the range the builder opened with: editing "Opens on" changes what the next opening
  // shows, not the window of a preview already read (AC135).
  // biome-ignore lint/correctness/useExhaustiveDependencies: applied once, on opening
  useEffect(() => {
    const window = addressWindow({}, defaultRange);
    if (window !== undefined) setScreenOverride({ window });
    setSettled(true);
    return () => {
      setSettled(false);
      clearScreenOverride();
    };
  }, []);
  return settled;
}

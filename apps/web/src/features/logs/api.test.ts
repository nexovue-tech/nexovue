import { describe, expect, it } from "vitest";

import type { LiveWindow } from "@/shared/selection";

import { logFacetsQueryKey, logSearchQueryKey, NO_FILTERS } from "./api";

const PROJECT = "01a0805b-2880-7cac-a92f-edcd8a2cd11f";

/** What `useLiveWindow` hands a screen: a window that moves, under a key that does not. */
function live(from: string, to: string): LiveWindow {
  return { from, to, key: "15m", refetchInterval: 10_000, isAbsolute: false };
}

const AT_NOON = live("2026-09-14T11:45:00.000Z", "2026-09-14T12:00:00.000Z");
const TEN_SECONDS_LATER = live("2026-09-14T11:45:10.000Z", "2026-09-14T12:00:10.000Z");

describe("what makes a logs read a different read", () => {
  it("keeps one cache key while the live window advances", () => {
    /* **The regression.** This key was built from `from` and `to` directly while
       every other window-scoped query in the app used `windowKeyOf`. The window
       advances every ten seconds, so the key changed every ten seconds, so
       TanStack saw a query it had never run: `isPending` went true and the logs
       screen drew its fourteen-row skeleton over lines that were already on it —
       for ever, and while the tail was paused, because a paused tail does not
       stop the window advancing. */
    expect(logSearchQueryKey(PROJECT, TEN_SECONDS_LATER, NO_FILTERS)).toEqual(
      logSearchQueryKey(PROJECT, AT_NOON, NO_FILTERS),
    );
  });

  it("is a different read when the reader picks a different window", () => {
    const pinned: LiveWindow = {
      ...AT_NOON,
      key: "2026-09-14T11:45:00.000Z/2026-09-14T12:00:00.000Z",
      isAbsolute: true,
    };

    expect(logSearchQueryKey(PROJECT, pinned, NO_FILTERS)).not.toEqual(
      logSearchQueryKey(PROJECT, AT_NOON, NO_FILTERS),
    );
  });

  it("is a different read when a filter changes", () => {
    expect(logSearchQueryKey(PROJECT, AT_NOON, { ...NO_FILTERS, query: "timeout" })).not.toEqual(
      logSearchQueryKey(PROJECT, AT_NOON, NO_FILTERS),
    );
    expect(
      logSearchQueryKey(PROJECT, AT_NOON, { ...NO_FILTERS, serviceName: "checkout" }),
    ).not.toEqual(logSearchQueryKey(PROJECT, AT_NOON, NO_FILTERS));
    expect(logSearchQueryKey(PROJECT, AT_NOON, { ...NO_FILTERS, minSeverity: 17 })).not.toEqual(
      logSearchQueryKey(PROJECT, AT_NOON, NO_FILTERS),
    );
  });

  it("holds the facets to the same rule, which they already were", () => {
    expect(logFacetsQueryKey(PROJECT, TEN_SECONDS_LATER)).toEqual(
      logFacetsQueryKey(PROJECT, AT_NOON),
    );
  });
});

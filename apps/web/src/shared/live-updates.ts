import { useEffect, useRef, useState } from "react";

/**
 * How a screen changes under a reader who did not ask it to.
 *
 * **Never move what someone is reading; mark what changed.** A live screen does not need
 * loud updates if it keeps a memory of what changed — loud updates are what an interface
 * builds when it forgets. Everything here is that memory.
 *
 * The damage a ten-second refresh does is not the redraw. It is a row changing places
 * under a cursor already reaching for it, and a number that quietly became a different
 * number while somebody was reading the one beside it.
 */

/** Long enough to catch an eye that was elsewhere, short enough not to nag. */
const HIGHLIGHT_MS = 1_200;

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * True for a moment after `value` changes, so a cell can mark itself.
 *
 * The **cell**, not the row. A row that lights up because one of six numbers moved sends
 * a reader hunting for which; the point of marking a change is to answer that without
 * being asked.
 *
 * Never true on first render: everything is new the first time, and a screen that lights
 * up entirely on arrival has said nothing.
 */
export function useJustChanged(value: unknown): boolean {
  const previous = useRef(value);
  const [changed, setChanged] = useState(false);

  useEffect(() => {
    if (Object.is(previous.current, value)) return;
    previous.current = value;

    if (prefersReducedMotion()) return;
    setChanged(true);
    const done = setTimeout(() => setChanged(false), HIGHLIGHT_MS);
    return () => clearTimeout(done);
  }, [value]);

  return changed;
}

export interface HeldOrder<T> {
  /** What to render: the newest values, in the order already on screen. */
  items: T[];
  /** True when the ranking has moved and is being withheld. */
  reorderPending: boolean;
}

function sameOrder(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((key, index) => key === right[index]);
}

/**
 * The newest readings, in the order the reader is already looking at.
 *
 * **A ranked list reorders itself, and that is the actual harm in a live screen.** A
 * customer reaching for the third row finds a different service under the cursor because
 * a refresh landed between deciding and clicking. Values may change freely — those are
 * the reading — but position is what a hand is aiming at.
 *
 * Rows that were not there before go to the end rather than into their ranked position:
 * a new row appearing mid-list pushes everything below it down, which is the same harm by
 * another route. `reorderPending` is how the screen offers to apply the real ranking,
 * which is the reader's to ask for.
 */
export function heldOrder<T>(
  previousKeys: readonly string[],
  incoming: readonly T[],
  keyOf: (item: T) => string,
): HeldOrder<T> {
  if (previousKeys.length === 0) return { items: [...incoming], reorderPending: false };

  const position = new Map(previousKeys.map((key, index) => [key, index]));
  const known: T[] = [];
  const arrived: T[] = [];
  for (const item of incoming) {
    (position.has(keyOf(item)) ? known : arrived).push(item);
  }
  known.sort((left, right) => (position.get(keyOf(left)) ?? 0) - (position.get(keyOf(right)) ?? 0));

  const held = [...known, ...arrived];
  return {
    items: held,
    reorderPending: !sameOrder(held.map(keyOf), incoming.map(keyOf)),
  };
}

/**
 * How many of `incoming` the reader has not been shown.
 *
 * For a feed, where new rows arrive at the top and inserting them moves everything a
 * reader is looking at. At the top of a feed there is nothing above to disturb, so rows
 * stream in; scrolled away, they are counted and held until asked for.
 */
export function unseenCount(shownKeys: readonly string[], incomingKeys: readonly string[]): number {
  if (shownKeys.length === 0) return 0;
  const shown = new Set(shownKeys);
  return incomingKeys.filter((key) => !shown.has(key)).length;
}

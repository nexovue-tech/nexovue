/**
 * The sentence each profiling view leads with (principles rule 1): what the graph
 * says, in words, before anyone has to read the graph. The graph is the evidence.
 */

import type { FunctionChange, FunctionShare } from "./api";
import { typePhrase } from "./profile-words";

/** Below this share, no single function "stands out" — the cost is spread. */
const STANDS_OUT = 0.1;

/** From this share up, one function is the whole profile, and "all of" says so. */
const ALL = 0.995;

/** A change in share smaller than this — half a percentage point — is no change. */
const MOVED = 0.005;

/** Past this, the newest profile is old enough that profiling may have stopped. */
export const STALE_AFTER_MS = 10 * 60_000;

function named(row: { name: string; unsymbolized: boolean }): string {
  return row.unsymbolized ? `unsymbolized native code in ${row.name}` : row.name;
}

function percent(share: number): string {
  const value = share * 100;
  return value < 10 ? `${value.toFixed(1)} %` : `${Math.round(value)} %`;
}

/**
 * Where the profile's total went, as one sentence: the function that spent the most
 * itself, and its share — or that nothing stands out, which is an answer too.
 */
export function readingOf(
  service: string,
  sampleType: string,
  total: number,
  functions: readonly FunctionShare[],
): string {
  const what = `${service}'s ${typePhrase(sampleType)}`;
  const top = [...functions].sort((a, b) => b.self_value - a.self_value)[0];
  if (!top || total <= 0) return `Nothing was recorded for ${what}.`;
  const share = top.self_value / total;
  if (share < STANDS_OUT) {
    return `No single function stands out in ${what}: the largest, ${named(top)}, is ${percent(share)} of it.`;
  }
  if (share >= ALL) return `${named(top)} accounts for all of ${what}.`;
  return `${named(top)} accounts for ${percent(share)} of ${what} — more than any other function.`;
}

/**
 * What changed between two profiles, as one sentence led by its two sides — "From
 * version 1.0.0 to 2.0.0" —: the function whose own share
 * grew most, and the one that shrank most — or that nothing moved.
 */
export function changeReadingOf(lead: string, functions: readonly FunctionChange[]): string {
  const grew = functions
    .filter((row) => row.change >= MOVED)
    .sort((a, b) => b.change - a.change)[0];
  const shrank = functions
    .filter((row) => row.change <= -MOVED)
    .sort((a, b) => a.change - b.change)[0];
  if (!grew && !shrank) return `${lead}, no function's share moved by more than half a point.`;
  const parts: string[] = [];
  if (grew) {
    parts.push(
      `${named(grew)} grew most, from ${percent(grew.baseline_self)} to ${percent(grew.comparison_self)} of the total`,
    );
  }
  if (shrank) {
    parts.push(
      `${named(shrank)} shrank most, from ${percent(shrank.baseline_self)} to ${percent(shrank.comparison_self)}`,
    );
  }
  return `${lead}, ${parts.join("; ")}.`;
}

/** "No profile since …" when the newest is old enough that the profiler may have stopped. */
export function isStale(lastReceived: string, now = Date.now()): boolean {
  const at = Date.parse(lastReceived);
  return Number.isFinite(at) && now - at > STALE_AFTER_MS;
}

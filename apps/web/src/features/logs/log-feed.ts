/**
 * The live half of the log screen, as data rather than as React.
 *
 * Two things happen here and nothing else does: new frames are folded into a bounded
 * newest-first list, and that list is merged with the page of stored records the search
 * returned.
 *
 * **A gap is a row, not a log line.** When the tail falls behind, the oldest lines are
 * discarded — by the server when a viewer stops reading, by this buffer when it is
 * full. Either way a marker is inserted *at the position the lines were lost from*, so
 * the screen shows where the hole is instead of quietly closing it. The whole point of
 * watching a tail is trusting that what is on screen is what happened.
 */

import { formatCount } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";

/** One stored record. Field names are the API's, unchanged. */
export interface LogLine {
  /** A hash of the record's own columns; `logs` has no primary key. */
  row_key: string;
  observed_at: string;
  service_name: string;
  severity: string;
  severity_number: number;
  body: string;
  /** Empty string when the record carried no trace context, never null. */
  trace_id: string;
  span_id: string;
  attributes: Record<string, unknown>;
}

export type LiveEntry =
  | { kind: "line"; key: string; line: LogLine }
  | { kind: "gap"; key: string; dropped: number };

export interface LiveBuffer {
  /** Newest first, the order the screen renders. */
  entries: readonly LiveEntry[];
  /** Every line this screen has been told it lost, for the header to state once. */
  droppedTotal: number;
}

export const EMPTY_LIVE: LiveBuffer = { entries: [], droppedTotal: 0 };

/** How many lines the screen holds before it starts dropping its own oldest. */
export const LIVE_CAPACITY = 2_000;

let gapSequence = 0;

/** A gap has no identity of its own, and two gaps must not share a virtualizer key. */
function gapEntry(dropped: number): LiveEntry {
  gapSequence += 1;
  return { kind: "gap", key: `gap-${gapSequence}`, dropped };
}

function countLines(entries: readonly LiveEntry[]): number {
  return entries.reduce((total, entry) => total + (entry.kind === "line" ? 1 : 0), 0);
}

/**
 * Fold one frame into the buffer.
 *
 * The server sends a frame oldest-first and reports what it discarded to make room;
 * those lines were older than everything in the frame and newer than everything already
 * held, so the marker goes exactly between the two.
 */
export function acceptFrame(
  buffer: LiveBuffer,
  frame: { records: readonly LogLine[]; dropped: number },
  capacity = LIVE_CAPACITY,
): LiveBuffer {
  const arriving: LiveEntry[] = frame.records
    .map((line): LiveEntry => ({ kind: "line", key: line.row_key, line }))
    .reverse();

  const entries = [
    ...arriving,
    ...(frame.dropped > 0 ? [gapEntry(frame.dropped)] : []),
    ...buffer.entries,
  ];

  return trim({ entries, droppedTotal: buffer.droppedTotal + frame.dropped }, capacity);
}

/**
 * Discard from the old end until the buffer fits, recording what went.
 *
 * The lines removed are the boundary between what is live and what the search already
 * shows, which is where the marker they leave behind belongs.
 *
 * Two counts, not one: `lost` is lines this trim discarded, and `carried` is what a gap
 * it swallowed was already reporting. Only `lost` is new, so only `lost` moves the
 * running total — but the marker left behind has to show both, or the hole shrinks
 * every time the buffer wraps.
 */
function trim(buffer: LiveBuffer, capacity: number): LiveBuffer {
  const entries = [...buffer.entries];
  let lost = 0;
  let carried = 0;

  while (countLines(entries) > capacity) {
    const oldest = entries.pop();
    if (oldest === undefined) break;
    if (oldest.kind === "line") lost += 1;
    else carried += oldest.dropped;
  }
  if (lost === 0 && carried === 0) return { ...buffer, entries };

  // Two gaps with nothing between them are one hole, and must read as one.
  const trailing = entries[entries.length - 1];
  if (trailing?.kind === "gap") {
    entries.pop();
    carried += trailing.dropped;
  }

  return {
    entries: [...entries, gapEntry(lost + carried)],
    droppedTotal: buffer.droppedTotal + lost,
  };
}

export type LogRow =
  | { kind: "line"; key: string; line: LogLine; live: boolean }
  | { kind: "gap"; key: string; dropped: number };

/**
 * What the list renders: the live lines, then the searched page under them.
 *
 * Two problems are solved here, and they pull in opposite directions.
 *
 * **The overlap.** A record can be in both halves — the tail starts a few seconds back
 * and the search ran a moment ago — so a line the tail already showed must not appear
 * again underneath it.
 *
 * **The genuine duplicate.** `row_key` is a hash of the record's own columns, because
 * `logs` has no primary key, so two byte-identical records written at the same instant
 * share one. The dev store holds pairs of exactly that shape, from a double insert
 * before the deduplication window existed. Dropping every searched line whose key is
 * live would delete the second copy of a line that really was written twice.
 *
 * So the overlap is skipped by *count*: as many searched copies as the tail is holding,
 * and no more. The render key then carries an occurrence suffix, because two rows on
 * one list may not share a key however identical their contents.
 */
export function buildLogRows(live: LiveBuffer, searched: readonly LogLine[]): LogRow[] {
  const heldLive = new Map<string, number>();
  for (const entry of live.entries) {
    if (entry.kind === "line") heldLive.set(entry.key, (heldLive.get(entry.key) ?? 0) + 1);
  }

  const occurrences = new Map<string, number>();
  const renderKey = (rowKey: string) => {
    const before = occurrences.get(rowKey) ?? 0;
    occurrences.set(rowKey, before + 1);
    return before === 0 ? rowKey : `${rowKey}#${before}`;
  };

  const rows: LogRow[] = live.entries.map((entry) =>
    entry.kind === "line"
      ? { kind: "line", key: renderKey(entry.key), line: entry.line, live: true }
      : { kind: "gap", key: entry.key, dropped: entry.dropped },
  );

  for (const line of searched) {
    const stillLive = heldLive.get(line.row_key) ?? 0;
    if (stillLive > 0) {
      heldLive.set(line.row_key, stillLive - 1);
      continue;
    }
    rows.push({ kind: "line", key: renderKey(line.row_key), line, live: false });
  }

  return rows;
}

/**
 * What the tail is doing, in the three states the sentence has words for.
 *
 * Narrower than the socket's own status on purpose: "connecting" and "ended" are not
 * "paused", and a sentence that called them that would be telling the reader something
 * false about why lines have stopped appearing.
 */
export type TailNarration = "live" | "paused" | "off";

/** The sentence under the heading. Says what is on screen, not how it was fetched. */
export function describeLogs(
  rowCount: number,
  windowLabel: string,
  tail: TailNarration,
  droppedTotal: number,
): string {
  const lines = `${formatCount(rowCount)} ${plural(rowCount, "line")}`;
  const scope = `${lines} from ${windowLabel}, newest first`;
  const sentence =
    tail === "live"
      ? `${scope}, updating as they arrive.`
      : tail === "paused"
        ? `${scope}. The tail is paused.`
        : `${scope}.`;

  if (droppedTotal === 0) return sentence;
  const dropped = `${formatCount(droppedTotal)} ${plural(droppedTotal, "line was", "lines were")}`;
  return `${sentence} ${dropped} dropped while the tail was behind — each gap is marked.`;
}

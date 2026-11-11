import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { formatZonedTime } from "@/shared/format/zoned";

import { CorrelationId } from "./PanelFrame";
import type { Action, LineAction, PageLines, SevereLine } from "./page-lines";

/**
 * The page's own words about itself, above the grid (F20, FR48, T6.6). What each says,
 * and which wins, is `page-lines.ts`; this draws them.
 *
 * Every action is a 44 px target below `md` (FR50, AC108). The severe line is the only
 * live region that interrupts: the stale banner reads across a room, at the design
 * system's largest heading size.
 */

const BUTTON = "inline-flex min-h-11 items-center underline underline-offset-2 md:min-h-0";
/** Severe lines that interrupt a screen reader: the rest are read in turn. */
const ALERTING: ReadonlySet<SevereLine["kind"]> = new Set([
  "not-current",
  "timeout",
  "refused",
  "outage",
]);

export function PageLine({
  lines,
  onAction,
  picker,
  canSetUp,
}: {
  lines: PageLines;
  onAction: (action: LineAction) => void;
  /** The service picker, held in the line that asks for it (FR44, AC140). */
  picker?: ReactNode;
  canSetUp: boolean;
}) {
  const { severe, state, context } = lines;
  return (
    <div className="flex flex-col gap-2">
      {severe ? <Severe line={severe} onAction={onAction} /> : null}
      {state ? (
        <div className="flex flex-wrap items-center gap-3 rounded-sm border border-line bg-raised px-3 py-2 text-ink text-sm">
          <p>{state.words}</p>
          {state.kind === "picker" ? picker : null}
          {state.setup && canSetUp ? (
            <Link to="/settings/api-keys" className={`text-brand ${BUTTON}`}>
              Set up
            </Link>
          ) : null}
          {state.action ? <ActionButton action={state.action} onAction={onAction} /> : null}
        </div>
      ) : null}
      {context.length > 0 ? (
        // Merged into one compact line, so every condition at once still leaves the first panel in view.
        <p className="flex flex-wrap items-center gap-x-3 text-ink-dim text-sm">
          {context.map((segment) => (
            <span key={segment.kind} className="inline-flex flex-wrap items-center gap-x-2">
              {segment.words}
              <ActionButton action={segment.action} onAction={onAction} />
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}

function ActionButton({
  action,
  onAction,
}: {
  action: Action;
  onAction: (action: LineAction) => void;
}) {
  return (
    <button type="button" onClick={() => onAction(action.id)} className={`text-brand ${BUTTON}`}>
      {action.label}
    </button>
  );
}

function Severe({ line, onAction }: { line: SevereLine; onAction: (action: LineAction) => void }) {
  const banner = line.kind === "not-current";
  const tone = ALERTING.has(line.kind)
    ? "border-warning-border bg-warning-subtle text-warning-text"
    : "border-line bg-raised text-ink-dim";
  return (
    <div
      role={ALERTING.has(line.kind) ? "alert" : "status"}
      className={`flex flex-wrap items-center gap-3 rounded-sm border px-3 py-2 ${tone} ${
        // The stale banner is the later, louder form of the outage line: read from across a room.
        banner ? "font-semibold text-2xl" : "text-sm"
      }`}
    >
      <p>{line.words}</p>
      {line.action ? <ActionButton action={line.action} onAction={onAction} /> : null}
      {line.correlationId ? <CorrelationId value={line.correlationId} /> : null}
    </div>
  );
}

/**
 * What must stay in reach while a long page scrolls, on a phone (FR50, FR64): the picker,
 * the age of the last good reading, and "6 of 30 panels could not be read", a link that
 * moves focus to the first. Hidden when there is nothing to say.
 */
export function StickyLine({
  lastGoodAt,
  zone,
  failing,
  unreadCount,
  totalCount,
  onFocusFirst,
  picker,
}: {
  lastGoodAt: string | undefined;
  zone: string;
  /** Reads have failed since the last good one: only then is the age shown (FR31). */
  failing: boolean;
  unreadCount: number;
  totalCount: number;
  onFocusFirst: () => void;
  picker?: ReactNode;
}) {
  const showAge = failing && lastGoodAt !== undefined;
  if (!showAge && unreadCount === 0 && !picker) return null;
  return (
    <div
      data-sticky-line
      className="sticky top-0 z-20 -mx-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-line border-b bg-page px-4 py-1.5 text-sm md:static md:mx-0 md:border-b-0 md:p-0"
    >
      {picker}
      {showAge ? (
        <span className="text-ink-dim">
          Last good reading {formatZonedTime(lastGoodAt, { zone })}
        </span>
      ) : null}
      {unreadCount > 0 ? (
        <button
          type="button"
          onClick={onFocusFirst}
          className="inline-flex min-h-11 items-center text-warning-text underline md:min-h-0"
        >
          {unreadCount} of {totalCount} panels could not be read
        </button>
      ) : null}
    </div>
  );
}

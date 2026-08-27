import { ChevronDown, ChevronRight } from "lucide-react";

import { formatDuration } from "@/shared/format/duration";

import { spanFailed } from "./api";
import type { Timeline } from "./trace-timeline";
import { placeSpan } from "./trace-timeline";
import type { WaterfallRow as Row } from "./trace-tree";

/** Fixed, so the virtualizer never has to measure and the vertical rhythm never breaks. */
export const ROW_HEIGHT = 28;

/**
 * The indent step is a CSS variable, not a number, because it has to shrink on a narrow
 * screen and the row cannot ask how wide the viewport is. TraceWaterfall sets it.
 */
const INDENT_STEP = "var(--waterfall-indent)";
/** Past this the indent costs more room than the depth is worth; the number takes over. */
const MAX_INDENT_LEVELS = 10;

/**
 * One span, one line.
 *
 * Depth is indent, duration is bar length, position is when it happened, service is a
 * label. Colour appears only where a span failed — everything healthy is type and space,
 * which is what keeps two hundred rows readable.
 *
 * The ancestor guides are drawn as a repeating gradient rather than as one element per
 * level: at sixty visible rows that is sixty nodes instead of several hundred, and the
 * gradient stops exactly at this row's own depth.
 */
export function WaterfallRow({
  row,
  timeline,
  offsetTop,
  selected,
  collapsed,
  onSelect,
  onToggleCollapse,
}: {
  row: Row;
  timeline: Timeline;
  offsetTop: number;
  selected: boolean;
  collapsed: boolean;
  onSelect: () => void;
  onToggleCollapse: () => void;
}) {
  const { span, depth, childCount, orphaned, precedesParent } = row;
  const failed = spanFailed(span);
  const { offsetPercent, widthPercent } = placeSpan(span, timeline);
  const indentLevels = Math.min(depth, MAX_INDENT_LEVELS);
  const indent = `calc(${INDENT_STEP} * ${indentLevels})`;

  return (
    <div
      className="absolute inset-x-0 top-0"
      style={{ height: ROW_HEIGHT, transform: `translateY(${offsetTop}px)` }}
    >
      <div
        className={`flex h-full items-stretch border-line-subtle border-b text-sm ${
          selected ? "bg-active" : "hover:bg-hover"
        }`}
      >
        <div
          className="relative flex w-[52%] shrink-0 items-center gap-1.5 pr-2 md:w-[42%] md:pr-3"
          style={{
            paddingLeft: `calc(${indent} + 4px)`,
            backgroundImage: `repeating-linear-gradient(to right, var(--color-line-subtle) 0 1px, transparent 1px ${INDENT_STEP})`,
            backgroundSize: `${indent} 100%`,
            backgroundRepeat: "no-repeat",
          }}
        >
          {childCount > 0 ? (
            <button
              type="button"
              onClick={onToggleCollapse}
              aria-label={
                collapsed
                  ? `Expand ${childCount} ${childCount === 1 ? "span" : "spans"} under ${span.span_name}`
                  : `Collapse ${span.span_name}`
              }
              aria-expanded={!collapsed}
              className="shrink-0 rounded-xs text-ink-dim hover:text-ink"
            >
              {collapsed ? (
                <ChevronRight size={12} aria-hidden="true" />
              ) : (
                <ChevronDown size={12} aria-hidden="true" />
              )}
            </button>
          ) : (
            <span aria-hidden="true" className="w-3 shrink-0" />
          )}

          <button
            type="button"
            onClick={onSelect}
            className="flex min-w-0 flex-1 items-baseline gap-2 text-left"
          >
            <span className="hidden shrink-0 font-mono text-2xs text-ink-dim md:inline">
              {span.service_name}
            </span>
            <span className={`truncate ${failed ? "text-critical-text" : "text-ink"}`}>
              {span.span_name}
            </span>
            {depth > MAX_INDENT_LEVELS ? (
              <span className="shrink-0 font-mono text-2xs text-ink-dim">·{depth}</span>
            ) : null}
          </button>
        </div>

        <button
          type="button"
          onClick={onSelect}
          aria-label={`${span.span_name}, ${formatDuration(span.duration_ms)}`}
          className="relative min-w-0 flex-1 border-line-subtle border-l"
        >
          <span
            className={`absolute top-1/2 h-2 -translate-y-1/2 rounded-xs ${
              failed ? "bg-critical-solid" : "bg-solid"
            }`}
            style={{ left: `${offsetPercent}%`, width: `${widthPercent}%` }}
          />
        </button>

        <div className="flex w-16 shrink-0 items-center justify-end gap-2 pr-3 pl-2 md:w-28">
          {orphaned ? (
            <span
              title="This span's parent is not in the trace"
              className="hidden font-mono text-2xs text-warning-text uppercase tracking-caps md:inline"
            >
              detached
            </span>
          ) : null}
          {precedesParent ? (
            <span
              title="This span starts before its parent — the two clocks disagree"
              className="hidden font-mono text-2xs text-warning-text uppercase tracking-caps md:inline"
            >
              skew
            </span>
          ) : null}
          <span className="font-mono text-2xs text-ink-dim">
            {formatDuration(span.duration_ms)}
          </span>
        </div>
      </div>
    </div>
  );
}

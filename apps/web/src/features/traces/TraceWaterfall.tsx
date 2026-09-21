import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useMemo, useRef, useState } from "react";

import { formatDurationOn } from "@/shared/format/duration";

import type { Span } from "./api";
import { buildTimeline, timelineTicks } from "./trace-timeline";
import { buildSpanTree, collapsibleSpanIds, flattenTree } from "./trace-tree";
import { ROW_HEIGHT, WaterfallRow } from "./WaterfallRow";

/** Rows kept in the DOM beyond the viewport, so a fast scroll never shows a blank band. */
const OVERSCAN_ROWS = 12;
const TICK_COUNT = 4;

/**
 * The waterfall.
 *
 * Every bar is drawn against one absolute scale, so a child that overruns its parent
 * simply overruns it on screen — the clock disagreement is information, not something to
 * hide. Rows are a fixed height, which is what lets two hundred spans virtualize without
 * a single measurement pass.
 */
export function TraceWaterfall({
  spans,
  selectedSpanId,
  onSelectSpan,
}: {
  spans: Span[];
  selectedSpanId?: string;
  onSelectSpan: (spanId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set<string>());

  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const timeline = useMemo(() => buildTimeline(spans), [spans]);
  const rows = useMemo(() => flattenTree(roots, collapsed), [roots, collapsed]);
  const ticks = useMemo(() => timelineTicks(timeline, TICK_COUNT), [timeline]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    getItemKey: (index) => rows[index].key,
  });

  const toggleCollapse = useCallback((spanId: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (!next.delete(spanId)) next.add(spanId);
      return next;
    });
  }, []);

  const allCollapsible = useMemo(() => collapsibleSpanIds(flattenTree(roots)), [roots]);
  const everythingCollapsed = collapsed.size >= allCollapsible.size && allCollapsible.size > 0;

  return (
    // The indent step lives here so one declaration governs the padding, the ancestor
    // guides and the breakpoint at which depth has to cost less room.
    <div className="flex min-h-0 flex-1 flex-col [--waterfall-indent:10px] md:[--waterfall-indent:14px]">
      <div className="flex shrink-0 items-stretch border-line border-b bg-surface">
        <div className="flex w-[52%] shrink-0 items-center justify-between gap-2 py-1.5 pr-2 pl-1 md:w-[42%] md:pr-3">
          {/* The trace header already counts the spans; on a phone that is enough. */}
          <span className="hidden font-mono text-2xs text-ink-dim uppercase tracking-caps md:inline">
            {rows.length} spans
          </span>
          {allCollapsible.size > 0 ? (
            <button
              type="button"
              onClick={() =>
                setCollapsed(everythingCollapsed ? new Set() : new Set(allCollapsible))
              }
              className="font-mono text-2xs text-ink-dim uppercase tracking-caps hover:text-ink"
            >
              {everythingCollapsed ? "Expand all" : "Collapse all"}
            </button>
          ) : null}
        </div>

        <div className="relative min-w-0 flex-1 border-line-subtle border-l">
          {ticks.slice(0, TICK_COUNT).map((tick, index) => (
            <span
              key={tick.percent}
              className={`absolute top-1.5 pl-1 font-mono text-2xs text-ink-dim ${
                index % 2 === 1 ? "hidden md:inline" : ""
              }`}
              style={{ left: `${tick.percent}%` }}
            >
              {formatDurationOn(tick.atMs, timeline.spanMs)}
            </span>
          ))}
        </div>

        <div className="w-16 shrink-0 py-1.5 pr-3 text-right font-mono text-2xs text-ink-dim uppercase tracking-caps md:w-28">
          Took
        </div>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
          {/* One overlay for the whole list rather than gridlines per row. */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-16 left-[52%] md:right-28 md:left-[42%]"
            style={{
              backgroundImage:
                "repeating-linear-gradient(to right, var(--color-line-subtle) 0 1px, transparent 1px 25%)",
            }}
          />

          {virtualizer.getVirtualItems().map((item) => {
            const row = rows[item.index];
            return (
              <WaterfallRow
                key={item.key}
                row={row}
                timeline={timeline}
                offsetTop={item.start}
                selected={row.span.span_id === selectedSpanId}
                collapsed={collapsed.has(row.span.span_id)}
                onSelect={() => onSelectSpan(row.span.span_id)}
                onToggleCollapse={() => toggleCollapse(row.span.span_id)}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

import { Link } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatDuration } from "@/shared/format/duration";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { resolveWindow, timeRangeById, useSelection } from "@/shared/selection";

import type { TraceSummary } from "./api";
import { useTraceList } from "./api";

const ROW_HEIGHT = 36;
const OVERSCAN_ROWS = 10;

/**
 * Every assembled trace in the window, newest first.
 *
 * One line each, no cards: service and name carry the identity, the numbers sit in
 * aligned columns so the eye scans down them, and the only colour on the screen marks
 * the traces that contain a failure.
 */
export function TraceListScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useMemo(() => resolveWindow(rangeId), [rangeId]);
  const list = useTraceList(projectId, timeWindow);

  const traces = list.data?.traces ?? [];

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: traces.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    getItemKey: (index) => traces[index].trace_id,
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Traces</h1>
        <p className="mt-1 text-ink-dim text-sm">
          Every request assembled in the last {timeRangeById(rangeId).label}, newest first.
        </p>
      </header>

      {list.isPending ? (
        <div className="px-4">
          <SkeletonRows rows={12} label="Loading traces" />
        </div>
      ) : list.isError ? (
        <div className="px-4">
          <InlineFailure
            title="Traces could not be loaded"
            error={list.error}
            onRetry={() => list.refetch()}
          />
        </div>
      ) : traces.length === 0 ? (
        <EmptyState
          title="No traces in this window"
          detail="Nothing has been assembled for the selected period. Widen the time range, or check that your services are still exporting."
        />
      ) : (
        <>
          <div className="flex shrink-0 items-center border-line border-b bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
            <span className="min-w-0 flex-1">Request</span>
            <span className="hidden w-24 text-right sm:block">Spans</span>
            <span className="w-20 text-right">Took</span>
            <span className="w-20 text-right sm:w-24">When</span>
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
            <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((item) => (
                <TraceRow
                  key={item.key}
                  trace={traces[item.index]}
                  offsetTop={item.start}
                  height={ROW_HEIGHT}
                />
              ))}
            </div>

            {list.hasNextPage ? (
              <div className="flex justify-center border-line-subtle border-t py-4">
                <Button
                  variant="secondary"
                  pending={list.isFetchingNextPage}
                  pendingLabel="Loading…"
                  onClick={() => list.fetchNextPage()}
                >
                  Load older traces
                </Button>
              </div>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

function TraceRow({
  trace,
  offsetTop,
  height,
}: {
  trace: TraceSummary;
  offsetTop: number;
  height: number;
}) {
  const failed = trace.error_count > 0 || trace.status_code === "STATUS_CODE_ERROR";

  return (
    <div
      className="absolute inset-x-0 top-0"
      style={{ height, transform: `translateY(${offsetTop}px)` }}
    >
      <Link
        to="/traces/$traceId"
        params={{ traceId: trace.trace_id }}
        className={`flex h-full items-center border-line-subtle border-b border-l-2 px-4 text-sm hover:bg-hover ${
          failed ? "border-l-critical-solid" : "border-l-transparent"
        }`}
      >
        <span className="flex min-w-0 flex-1 items-baseline gap-2">
          {/* The name identifies the request; the service is the first thing to go when
              there is no room for both, and the trace screen names it immediately. */}
          <span className="hidden shrink-0 font-mono text-2xs text-ink-dim sm:inline">
            {trace.root_service}
          </span>
          <span className="truncate text-ink">{trace.root_name}</span>
        </span>

        <span className="hidden w-24 shrink-0 text-right font-mono text-2xs text-ink-dim sm:block">
          {trace.span_count}
          {trace.error_count > 0 ? (
            <span className="text-critical-text"> · {trace.error_count} failed</span>
          ) : null}
        </span>

        <span className="w-20 shrink-0 text-right font-mono text-2xs text-ink-dim">
          {formatDuration(trace.duration_ms)}
        </span>

        <span
          className="w-20 shrink-0 text-right font-mono text-2xs text-ink-dim sm:w-24"
          title={formatTimestamp(trace.start_time)}
        >
          {formatRelativeTime(trace.start_time)}
        </span>
      </Link>
    </div>
  );
}

import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatDuration } from "@/shared/format/duration";
import { formatTimestamp } from "@/shared/format/time";
import { useSelection } from "@/shared/selection";
import { useTrace } from "./api";
import { SpanDetail } from "./SpanDetail";
import { TraceWaterfall } from "./TraceWaterfall";
import { buildSpanTree, flattenTree } from "./trace-tree";

/**
 * One request end to end.
 *
 * The header answers what this trace was and how it went; the waterfall answers where
 * the time went; the panel answers what a single span actually did. Three questions,
 * three places, no cards.
 */
export function TraceScreen({ traceId }: { traceId: string }) {
  const { projectId } = useSelection();
  const trace = useTrace(projectId, traceId);
  const [selectedSpanId, setSelectedSpanId] = useState<string>();

  const spans = trace.data?.spans ?? [];

  // The panel needs the row, not the span: depth and the skew flags live on the row.
  const selectedRow = useMemo(() => {
    if (!selectedSpanId) return undefined;
    return flattenTree(buildSpanTree(spans)).find((row) => row.span.span_id === selectedSpanId);
  }, [spans, selectedSpanId]);

  if (trace.isPending) {
    return (
      <div className="px-4 py-6">
        <SkeletonRows rows={14} label="Loading trace" />
      </div>
    );
  }

  if (trace.isError) {
    return (
      <div className="px-4 py-6">
        <InlineFailure
          title="This trace could not be loaded"
          error={trace.error}
          onRetry={() => trace.refetch()}
        />
      </div>
    );
  }

  const detail = trace.data;
  const rootSpan = spans.find((span) => span.parent_span_id === "") ?? spans[0];

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="shrink-0 border-line border-b px-4 pt-5 pb-3">
          <Link
            to="/traces"
            className="font-mono text-2xs text-ink-dim uppercase tracking-caps hover:text-ink"
          >
            ← All traces
          </Link>

          <h1 className="mt-2 font-semibold font-serif text-ink text-xl tracking-tight">
            {rootSpan ? (
              <>
                <span className="text-ink-dim">{rootSpan.service_name}</span> {rootSpan.span_name}
              </>
            ) : (
              "Trace"
            )}
          </h1>

          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-ink-dim text-sm">
            <span>
              {formatDuration(detail.duration_ms)} · {detail.span_count}{" "}
              {detail.span_count === 1 ? "span" : "spans"}
            </span>
            {detail.error_count > 0 ? (
              <span className="text-critical-text">
                {detail.error_count} failed {detail.error_count === 1 ? "span" : "spans"}
              </span>
            ) : null}
            <span>{formatTimestamp(detail.start_time)}</span>
            <span className="flex items-center gap-2">
              <span className="select-all font-mono text-2xs">{detail.trace_id}</span>
              <CopyButton value={detail.trace_id} label="trace id" />
            </span>
          </div>
        </header>

        {spans.length === 0 ? (
          <EmptyState
            title="This trace has no spans"
            detail="The trace was recorded but nothing inside it arrived. If a service is still exporting, it may appear shortly."
          />
        ) : (
          <TraceWaterfall
            spans={spans}
            selectedSpanId={selectedSpanId}
            onSelectSpan={setSelectedSpanId}
          />
        )}
      </div>

      {selectedRow ? (
        <aside
          aria-label="Span detail"
          // A column beside the waterfall where there is width for one, a sheet over it
          // where there is not — at 375px a third column would leave no waterfall at all.
          className="fixed inset-x-0 bottom-0 z-20 h-[55dvh] border-line border-t bg-page shadow-overlay lg:static lg:h-auto lg:w-96 lg:shrink-0 lg:border-t-0 lg:border-l lg:shadow-none"
        >
          <SpanDetail row={selectedRow} onClose={() => setSelectedSpanId(undefined)} />
        </aside>
      ) : null}
    </div>
  );
}

import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef, useState } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import type { TimeWindow } from "@/shared/selection";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type { ErrorGroupDetail, ErrorGroupSummary } from "./api";
import { useErrorGroup, useErrorGroups } from "./api";
import { ErrorGroupPanel } from "./ErrorGroupDetail";
import { describeErrors, describeServices, formatOccurrences, isNewInWindow } from "./error-groups";

const ROW_HEIGHT = 46;
const OVERSCAN_ROWS = 8;

/* Column widths, declared once so the header and every row cannot drift apart. */
const SERVICES_COLUMN = "hidden w-40 shrink-0 lg:block";
const COUNT_COLUMN = "w-16 shrink-0 text-right";
const STARTED_COLUMN = "w-28 shrink-0 text-right";
const LAST_SEEN_COLUMN = "hidden w-24 shrink-0 text-right sm:block";

/**
 * Every exception in the window, grouped — a hundred identical crashes on one line.
 *
 * The route component owns the selection and the two queries; everything visible is in
 * `ErrorsView`, which is a function of its props and nothing else.
 */
export function ErrorsScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow(rangeId);

  const [selectedGroupId, setSelectedGroupId] = useState<string>();

  const groups = useErrorGroups(projectId, timeWindow);
  const detail = useErrorGroup(projectId, selectedGroupId, timeWindow);

  const listed = groups.data?.groups ?? [];
  // A group id is derived from the window's rows, so moving the window can retire the
  // selection. Falling back to nothing beats a panel describing a group that is gone.
  const selected = listed.find((group) => group.group_id === selectedGroupId);

  return (
    <ErrorsView
      windowLabel={timeRangeById(rangeId).label}
      timeWindow={timeWindow}
      groups={listed}
      isPending={groups.isPending}
      isError={groups.isError}
      error={groups.error}
      onRetry={() => void groups.refetch()}
      selected={selected}
      onSelect={setSelectedGroupId}
      detail={detail.data}
      detailIsPending={selected !== undefined && detail.isPending}
      detailIsError={detail.isError}
      detailError={detail.error}
      onDetailRetry={() => void detail.refetch()}
    />
  );
}

interface ErrorsViewProps {
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  timeWindow: TimeWindow;
  groups: readonly ErrorGroupSummary[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  selected?: ErrorGroupSummary;
  onSelect: (groupId: string | undefined) => void;
  detail?: ErrorGroupDetail;
  detailIsPending: boolean;
  detailIsError: boolean;
  detailError?: unknown;
  onDetailRetry: () => void;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function ErrorsView({
  windowLabel,
  timeWindow,
  groups,
  isPending,
  isError,
  error,
  onRetry,
  selected,
  onSelect,
  detail,
  detailIsPending,
  detailIsError,
  detailError,
  onDetailRetry,
}: ErrorsViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: groups.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    getItemKey: (index) => groups[index].group_id,
  });

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="shrink-0 px-4 pt-5 pb-3">
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Errors</h1>
          <p className="mt-1 text-ink-dim text-sm">
            {isPending || isError
              ? `Every exception thrown in the last ${windowLabel}, grouped, most frequent first.`
              : describeErrors(groups, windowLabel, timeWindow)}
          </p>
        </header>

        {isPending ? (
          <div className="px-4">
            <SkeletonRows rows={10} label="Loading errors" />
          </div>
        ) : isError ? (
          <div className="px-4">
            <InlineFailure title="Errors could not be loaded" error={error} onRetry={onRetry} />
          </div>
        ) : groups.length === 0 ? (
          <EmptyState
            title="Nothing threw in this window"
            detail="No exception reached us for the selected period, which is the result you want. If you expected one, widen the time range — an error that stopped an hour ago is invisible from a fifteen-minute window."
          />
        ) : (
          <>
            <ColumnHeader />

            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
              <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((item) => (
                  <GroupRow
                    key={item.key}
                    group={groups[item.index]}
                    timeWindow={timeWindow}
                    selected={groups[item.index].group_id === selected?.group_id}
                    onSelect={onSelect}
                    offsetTop={item.start}
                    height={item.size}
                  />
                ))}
              </div>
            </div>
          </>
        )}
      </div>

      {selected ? (
        <aside
          aria-label="Error detail"
          // A column beside the list where there is width for one, a sheet over it where
          // there is not — at 375px a second column would leave no list at all.
          className="fixed inset-x-0 bottom-0 z-20 h-[55dvh] border-line border-t bg-page shadow-overlay lg:static lg:h-auto lg:w-[26rem] lg:shrink-0 lg:border-t-0 lg:border-l lg:shadow-none"
        >
          <ErrorGroupPanel
            summary={selected}
            detail={detail?.group.group_id === selected.group_id ? detail : undefined}
            isPending={detailIsPending}
            isError={detailIsError}
            error={detailError}
            onRetry={onDetailRetry}
            onClose={() => onSelect(undefined)}
          />
        </aside>
      ) : null}
    </div>
  );
}

function ColumnHeader() {
  return (
    <div className="flex shrink-0 items-center border-line border-b bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
      <span className="min-w-0 flex-1">Exception</span>
      <span className={SERVICES_COLUMN}>Services</span>
      <span className={COUNT_COLUMN}>Count</span>
      <span className={STARTED_COLUMN}>Started</span>
      <span className={LAST_SEEN_COLUMN}>Last</span>
    </div>
  );
}

/**
 * One group: the exception, the message every instance of it collapsed into, and the
 * three numbers that say whether it is an incident or a long-standing bug.
 */
function GroupRow({
  group,
  timeWindow,
  selected,
  onSelect,
  offsetTop,
  height,
}: {
  group: ErrorGroupSummary;
  timeWindow: TimeWindow;
  selected: boolean;
  onSelect: (groupId: string) => void;
  offsetTop: number;
  height: number;
}) {
  const startedHere = isNewInWindow(group, timeWindow);

  return (
    <div
      className="absolute inset-x-0 top-0"
      style={{ height, transform: `translateY(${offsetTop}px)` }}
    >
      <button
        type="button"
        onClick={() => onSelect(group.group_id)}
        aria-current={selected}
        className={`flex h-full w-full items-center border-line-subtle border-b border-l-2 px-4 text-left text-sm hover:bg-hover ${
          // Every row here is an error, so a red rule on all of them marks nothing. It
          // marks the ones that began inside this window: an incident, not the backlog.
          startedHere ? "border-l-critical-solid" : "border-l-transparent"
        } ${selected ? "bg-raised" : ""}`}
      >
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate font-medium text-ink">{group.exception_type}</span>
          {/* The template is what makes a hundred crashes one row, so a truncated one
              stays readable on hover rather than only inside the panel. */}
          <span className="truncate text-ink-dim text-xs" title={group.message_template}>
            {group.message_template}
          </span>
        </span>

        <span className={`${SERVICES_COLUMN} truncate pl-3 font-mono text-2xs text-ink-dim`}>
          {describeServices(group.services)}
        </span>

        <span className={`${COUNT_COLUMN} font-mono text-2xs text-ink`}>
          {formatOccurrences(group.occurrences)}
        </span>

        <span
          className={`${STARTED_COLUMN} font-mono text-2xs ${
            startedHere ? "text-critical-text" : "text-ink-dim"
          }`}
          title={
            startedHere
              ? `First seen ${formatTimestamp(group.first_seen)}`
              : "Already happening when this window opened — widen the range to find where it began."
          }
        >
          {/* Colour never carries this alone: "before window" and a timestamp are
              different words, not the same word in two shades. */}
          {startedHere ? formatRelativeTime(group.first_seen) : "before window"}
        </span>

        <span
          className={`${LAST_SEEN_COLUMN} font-mono text-2xs text-ink-dim`}
          title={formatTimestamp(group.last_seen)}
        >
          {formatRelativeTime(group.last_seen)}
        </span>
      </button>
    </div>
  );
}

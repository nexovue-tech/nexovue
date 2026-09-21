import { useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import type { FilterDefinition, FilterValues } from "@/shared/components/FilterBar";
import { appliedFilters, FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { filterParams, parseFilterValues, withFilter } from "@/shared/filters";
import { formatCount, UNKNOWN } from "@/shared/format/number";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";
import type { ErrorGroupDetail, ErrorGroupSummary, GroupTrendPoint } from "./api";
import { useErrorGroup, useErrorGroups, useErrorGroupTrend } from "./api";
import { ErrorGroupPanel } from "./ErrorGroupDetail";
import {
  DEPLOY_IS_NOT_A_CAUSE,
  describeErrors,
  describePrecedingDeploy,
  describeServices,
  firstWithStacktrace,
  isNewInWindow,
} from "./error-groups";
import { errorFilters } from "./filters";
import type { SymbolicationState } from "./symbolication";
import { useSymbolication } from "./symbolication";

const ROW_HEIGHT = 46;
const OVERSCAN_ROWS = 8;

/* Column widths, declared once so the header and every row cannot drift apart. */
const SERVICES_COLUMN = "hidden w-40 shrink-0 pl-3 lg:block";
/* The last column to earn its width: a deploy is a lead, and the two facts beside it —
   what threw and when it started — are the ones nobody may lose to make room. */
const DEPLOY_COLUMN = "hidden w-32 shrink-0 pl-3 xl:block";
const COUNT_COLUMN = "w-16 shrink-0 text-right";
const STARTED_COLUMN = "w-28 shrink-0 text-right";
const LAST_SEEN_COLUMN = "hidden w-24 shrink-0 text-right sm:block";
/* 104px of sparkline plus its 12px gutter, so the drawing does not push into the row's
   own right padding. Reserved on every row so nothing shifts when a group has no trend.
   Gone below `sm`: it was taking 116px of a 390px screen while the exception — the one
   column somebody is reading — was squeezed to "RangeEr…"
   (`docs/quality/console-review-2026-09-20.md`). */
const TREND_COLUMN = "hidden w-29 shrink-0 pl-3 sm:block";

/**
 * Every exception in the window, grouped — a hundred identical crashes on one line.
 *
 * The route component owns the selection and the two queries; everything visible is in
 * `ErrorsView`, which is a function of its props and nothing else.
 */
export function ErrorsScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();
  const navigate = useNavigate();

  // The route's own id, not its path: `/errors` renders inside the shell.
  const search = useSearch({ from: "/shell/errors" });
  const [definitions, setDefinitions] = useState(() => errorFilters([]));
  const filters = useMemo(() => parseFilterValues(definitions, search), [definitions, search]);

  // `replace`, not push: the back button belongs to the screen a person came
  // from, not to every control they tried on the way.
  const show = (next: Record<string, string | undefined>) =>
    void navigate({ to: "/errors", search: () => filterParams(next), replace: true });

  const [selectedGroupId, setSelectedGroupId] = useState<string>();

  const groups = useErrorGroups(projectId, timeWindow, filterParams(filters));
  const listed = groups.data?.groups ?? [];

  // The options depend on what came back, and what came back depends on the
  // filters — so the definitions are rebuilt from each answer.
  useEffect(() => {
    if (listed.length > 0) setDefinitions(errorFilters(listed));
  }, [listed]);
  const trendByGroup = useErrorGroupTrend(projectId, timeWindow);
  const detail = useErrorGroup(projectId, selectedGroupId, timeWindow);

  // The stack the panel will show is the one symbolication is asked about, so
  // the carrier is picked here rather than inside the view.
  const carrier = firstWithStacktrace(detail.data?.recent ?? []);
  const symbolication = useSymbolication(
    projectId,
    carrier?.stacktrace ?? "",
    carrier?.release ?? "",
  );

  // A group id is derived from the window's rows, so moving the window can retire the
  // selection. Falling back to nothing beats a panel describing a group that is gone.
  const selected = listed.find((group) => group.group_id === selectedGroupId);

  return (
    <ErrorsView
      filterDefinitions={definitions}
      filters={filters}
      onFilterChange={(key, value) => show(withFilter(filters, key, value))}
      onClearFilters={() => show({})}
      windowLabel={windowPhrase}
      symbolication={symbolication}
      groups={listed}
      trendByGroup={trendByGroup}
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
  filterDefinitions: readonly FilterDefinition[];
  filters: FilterValues;
  onFilterChange: (key: string, value: string | undefined) => void;
  onClearFilters: () => void;
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  /** The resolved stack for the selected group, asked for by the route component. */
  symbolication: SymbolicationState;
  groups: readonly ErrorGroupSummary[];
  /** Keyed by group id. A group missing from it has no trend and renders the rule. */
  trendByGroup?: ReadonlyMap<string, readonly GroupTrendPoint[]>;
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
  filterDefinitions,
  filters,
  onFilterChange,
  onClearFilters,
  windowLabel,
  symbolication,
  groups,
  trendByGroup,
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
  const ceiling = trendCeiling(groups, trendByGroup);
  const narrowed = appliedFilters(filterDefinitions, filters).length > 0;

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
              ? `Every exception thrown in ${windowLabel}, grouped, most frequent first.`
              : describeErrors(groups, windowLabel)}
          </p>
        </header>

        <FilterBar
          definitions={filterDefinitions}
          values={filters}
          onChange={onFilterChange}
          onClearAll={onClearFilters}
        />

        {isPending ? (
          <div className="px-4">
            <SkeletonRows rows={10} label="Loading errors" />
          </div>
        ) : isError ? (
          <div className="px-4">
            <InlineFailure title="Errors could not be loaded" error={error} onRetry={onRetry} />
          </div>
        ) : groups.length === 0 ? (
          // Nothing threw and nothing *new* threw are different results, and only
          // one of them is the one you want.
          narrowed ? (
            <EmptyState
              title="Nothing new started in this window"
              detail="Exceptions were thrown in the selected period, but all of them were already running when it opened. That is a backlog rather than an incident."
              action={
                <Button variant="secondary" onClick={onClearFilters}>
                  Show every error
                </Button>
              }
            />
          ) : (
            <EmptyState
              title="Nothing threw in this window"
              detail="No exception reached us for the selected period, which is the result you want. If you expected one, widen the time range — an error that stopped an hour ago is invisible from a fifteen-minute window."
            />
          )
        ) : (
          <>
            <ColumnHeader />

            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
              <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((item) => (
                  <GroupRow
                    key={item.key}
                    group={groups[item.index]}
                    selected={groups[item.index].group_id === selected?.group_id}
                    onSelect={onSelect}
                    offsetTop={item.start}
                    height={item.size}
                    trend={trendByGroup?.get(groups[item.index].group_id)}
                    ceiling={ceiling}
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
            symbolication={symbolication}
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
      <span className={DEPLOY_COLUMN}>Deploy before</span>
      <span className={SERVICES_COLUMN}>Services</span>
      <span className={COUNT_COLUMN}>Count</span>
      <span className={STARTED_COLUMN}>Started</span>
      <span className={LAST_SEEN_COLUMN}>Last</span>
      <span className={TREND_COLUMN}>Trend</span>
    </div>
  );
}

/**
 * One group: the exception, the message every instance of it collapsed into, and the
 * three numbers that say whether it is an incident or a long-standing bug.
 */
/**
 * One scale for the screen, so two rows can be compared.
 *
 * Every row here is an exception, so unlike the service list there is no second kind of
 * series to scale separately — a group's occurrences are a group's occurrences.
 */
export function trendCeiling(
  groups: readonly ErrorGroupSummary[],
  trendByGroup: ReadonlyMap<string, readonly GroupTrendPoint[]> | undefined,
): number {
  let ceiling = 0;
  for (const group of groups) {
    for (const point of trendByGroup?.get(group.group_id) ?? []) {
      if (point.occurrences > ceiling) ceiling = point.occurrences;
    }
  }
  return ceiling;
}

function GroupRow({
  group,
  selected,
  onSelect,
  offsetTop,
  height,
  trend,
  ceiling,
}: {
  group: ErrorGroupSummary;
  selected: boolean;
  onSelect: (groupId: string) => void;
  offsetTop: number;
  height: number;
  trend?: readonly GroupTrendPoint[];
  ceiling: number;
}) {
  const startedHere = isNewInWindow(group);
  const deploy = group.preceding_deploy;
  const afterDeploy = describePrecedingDeploy(group);

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

        {/* The deploy before it, on the row rather than only in the panel: "did we
            ship something" is asked while scanning the list, not after opening one.
            Dimmed and abbreviated to the version, because it is a lead and not a
            finding — the panel carries the sentence saying what it is not. */}
        <span
          className={`${DEPLOY_COLUMN} truncate font-mono text-2xs text-ink-dim`}
          title={afterDeploy === undefined ? undefined : `${afterDeploy}. ${DEPLOY_IS_NOT_A_CAUSE}`}
        >
          {deploy === null ? UNKNOWN : `after ${deploy.version}`}
        </span>

        <span className={`${SERVICES_COLUMN} truncate font-mono text-2xs text-ink-dim`}>
          {describeServices(group.services)}
        </span>

        <span className={`${COUNT_COLUMN} font-mono text-2xs text-ink`}>
          {formatCount(group.occurrences)}
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

        <span className={TREND_COLUMN}>
          <Sparkline
            points={(trend ?? []).map((point) => point.occurrences)}
            max={ceiling}
            tone="critical"
            label={`${group.exception_type} occurrences over the window`}
          />
        </span>
      </button>
    </div>
  );
}

import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import type { FilterDefinition, FilterValues } from "@/shared/components/FilterBar";
import { appliedFilters, FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { filterParams, parseFilterValues, withFilter } from "@/shared/filters";
import { formatCount } from "@/shared/format/number";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type { BreakdownDimension, WebVitalBreakdown } from "./api";
import { useWebVitals, useWebVitalsBreakdown } from "./api";
import { rumFilters } from "./filters";
import type { BreakdownCell, ReadableGroup, ReadableVital } from "./vitals";
import {
  describeVitals,
  MIN_READINGS,
  ROUTE_TEMPLATE_TAG,
  readableGroup,
  readableVitals,
  VITALS,
} from "./vitals";

/* Every rating's colour, named once so a card and its footnote cannot disagree. */
const RATING_STYLES: Record<ReadableVital["rating"], string> = {
  good: "text-healthy",
  "needs-improvement": "text-warning-text",
  poor: "text-critical-text",
  insufficient: "text-ink-dim",
};

/* What each rating is called in words. Colour never carries the meaning alone. */
const RATING_WORDS: Record<ReadableVital["rating"], string> = {
  good: "Good",
  "needs-improvement": "Needs improvement",
  poor: "Poor",
  insufficient: "Not enough data",
};

/**
 * What real browsers experienced: each Core Web Vital at the 75th percentile.
 *
 * The route component owns the selection and the query; everything visible is in
 * `RumView`, which is a function of its props and nothing else.
 */
export function RumScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow();
  const navigate = useNavigate();

  // The route's own id, not its path: `/rum` renders inside the shell.
  const search = useSearch({ from: "/shell/rum" });

  // The page list is built from the breakdown that is already loaded, so the
  // definitions are rebuilt from each answer. `FilterBar` keeps a selected value
  // that is no longer offered, which is what stops the control and the results
  // disagreeing while a filter is applied.
  const [definitions, setDefinitions] = useState(() => rumFilters({}));
  const filters = useMemo(() => parseFilterValues(definitions, search), [definitions, search]);
  const narrowing = useMemo(() => filterParams(filters), [filters]);

  const vitals = useWebVitals(projectId, timeWindow, narrowing);
  // Both splits, because which one is worth offering is decided by how many
  // groups each came back with, and that is not knowable without asking.
  const byPage = useWebVitalsBreakdown(projectId, timeWindow, "page", narrowing);
  const byDevice = useWebVitalsBreakdown(projectId, timeWindow, "device", narrowing);

  const breakdowns: Breakdowns = useMemo(
    () => ({ page: byPage.data, device: byDevice.data }),
    [byPage.data, byDevice.data],
  );

  useEffect(() => {
    if ((breakdowns.page?.groups.length ?? 0) > 0) setDefinitions(rumFilters(breakdowns));
  }, [breakdowns]);

  // `replace`, not push: the back button belongs to the screen a person came
  // from, not to every control they tried on the way.
  const show = (next: Record<string, string | undefined>) =>
    void navigate({ to: "/rum", search: () => filterParams(next), replace: true });

  return (
    <RumView
      filterDefinitions={definitions}
      filters={filters}
      onFilterChange={(key, value) => show(withFilter(filters, key, value))}
      onClearFilters={() => show({})}
      windowLabel={timeRangeById(rangeId).label}
      vitals={readableVitals(vitals.data?.vitals ?? [])}
      breakdowns={breakdowns}
      isPending={vitals.isPending}
      isError={vitals.isError}
      error={vitals.error}
      onRetry={() => void vitals.refetch()}
    />
  );
}

/** Each split, once it has arrived. A failed or pending split is simply absent. */
export type Breakdowns = Partial<Record<BreakdownDimension, WebVitalBreakdown>>;

interface RumViewProps {
  filterDefinitions: readonly FilterDefinition[];
  filters: FilterValues;
  onFilterChange: (key: string, value: string | undefined) => void;
  onClearFilters: () => void;
  /** "hour", "24 hours" — the range picker's own word, so the two agree. */
  windowLabel: string;
  vitals: readonly ReadableVital[];
  breakdowns?: Breakdowns;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function RumView({
  filterDefinitions,
  filters,
  onFilterChange,
  onClearFilters,
  windowLabel,
  vitals,
  breakdowns = {},
  isPending,
  isError,
  error,
  onRetry,
}: RumViewProps) {
  const anyReadings = vitals.some((vital) => vital.readings > 0);
  const narrowed = appliedFilters(filterDefinitions, filters).length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
          Real user monitoring
        </h1>
        <p className="mt-1 text-ink-dim text-sm">
          {isPending || isError
            ? `What real browsers measured in the last ${windowLabel}.`
            : describeVitals(vitals, windowLabel)}
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
          <SkeletonRows rows={5} label="Loading web vitals" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure title="Web vitals could not be loaded" error={error} onRetry={onRetry} />
        </div>
      ) : !anyReadings ? (
        // "Nobody visited this page on a tablet" and "the SDK is not installed"
        // are opposite findings, and one sentence for both sends a reader to
        // reinstall something that is already working.
        narrowed ? (
          <EmptyState
            title="No readings match these filters"
            detail="Browsers reported in this window, but none of them match what you asked for. A page nobody loaded on that device has no percentile to show."
            action={
              <Button variant="secondary" onClick={onClearFilters}>
                Show every reading
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No browser has reported yet"
            detail="Core Web Vitals arrive from the browser SDK on real page loads, not from your servers. Add the script to your site, or widen the time range — a quiet hour on a low-traffic page looks exactly like this."
          />
        )
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6">
          <VitalGroup
            heading="Core Web Vitals"
            note="The three Google measures a site on."
            vitals={vitals.filter((vital) => vital.core)}
          />
          <VitalGroup
            heading="Diagnostics"
            note="Not scored, but they explain a slow LCP."
            vitals={vitals.filter((vital) => !vital.core)}
          />
          <VitalBreakdown breakdowns={breakdowns} />
        </div>
      )}
    </div>
  );
}

function VitalGroup({
  heading,
  note,
  vitals,
}: {
  heading: string;
  note: string;
  vitals: readonly ReadableVital[];
}) {
  return (
    <section className="mt-4 first:mt-2">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {heading} <span className="normal-case tracking-normal">— {note}</span>
      </h2>
      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {vitals.map((vital) => (
          <VitalCard key={vital.name} vital={vital} />
        ))}
      </div>
    </section>
  );
}

/**
 * One vital: the number, what it means, and how many page loads are behind it.
 *
 * The sample size is on the card rather than in a tooltip because it is what decides
 * whether the number should be believed, and a p75 over nine readings looks exactly
 * like a p75 over nine thousand.
 */
function VitalCard({ vital }: { vital: ReadableVital }) {
  const thin = vital.readings > 0 && vital.readings < MIN_READINGS;

  return (
    <article className="rounded-md border border-line bg-surface px-3.5 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium font-mono text-ink text-sm">{vital.label}</span>
        <span className={`text-2xs ${RATING_STYLES[vital.rating]}`}>
          {RATING_WORDS[vital.rating]}
        </span>
      </div>

      <p
        className={`mt-1.5 font-semibold font-serif text-2xl tabular-nums ${
          vital.rating === "insufficient" ? "text-ink-dim" : "text-ink"
        }`}
      >
        {vital.display}
      </p>

      <p className="mt-1 text-ink-dim text-xs">{vital.meaning}</p>

      <p className="mt-2 font-mono text-2xs text-ink-dim">
        {vital.readings === 0
          ? "no readings in this window"
          : thin
            ? `${formatCount(vital.readings)} readings — too few for a percentile`
            : `${formatCount(vital.readings)} readings`}
      </p>
    </article>
  );
}

/* ------------------------------------------------------------- the breakdown */

const DIMENSION_ORDER: readonly BreakdownDimension[] = ["page", "device"];

const DIMENSION_LABELS: Record<BreakdownDimension, string> = {
  page: "Page",
  device: "Device",
};

/* The columns, in the order `readableVitals` fixes, so a cell cannot land under
   the wrong heading. The two diagnostics fold away on a narrow screen. */
const BREAKDOWN_COLUMNS = VITALS.map(([name, definition]) => ({
  name,
  label: definition.label,
  core: definition.core,
}));

/* There is no geo split. `web_vitals.country` is a column nothing writes — there is
   no GeoIP anywhere in this system — so the row's original "by page/geo/device" is
   two questions the store can answer and one it cannot. */

/**
 * Where a bad site-wide number is actually coming from.
 *
 * Offered per dimension only when that split has more than one group: a site with one
 * page, or one that only desktop visited, would get a control that rearranges nothing.
 * When neither split has two groups the section is absent entirely rather than shown
 * empty — the cards above already say everything a single group would.
 */
function VitalBreakdown({ breakdowns }: { breakdowns: Breakdowns }) {
  const [chosen, setChosen] = useState<BreakdownDimension | null>(null);

  const offered = DIMENSION_ORDER.filter(
    (dimension) => (breakdowns[dimension]?.groups.length ?? 0) > 1,
  );
  if (offered.length === 0) return null;

  const selected = chosen !== null && offered.includes(chosen) ? chosen : offered[0];
  const breakdown = breakdowns[selected];
  if (breakdown === undefined) return null;

  const groups = breakdown.groups.map(readableGroup);

  return (
    <section className="mt-5">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        Breakdown{" "}
        <span className="normal-case tracking-normal">
          — which {selected === "page" ? "pages" : "devices"} the numbers above came from.
        </span>
      </h2>

      <DimensionBar
        breakdowns={breakdowns}
        offered={offered}
        selected={selected}
        onSelect={setChosen}
      />

      {breakdown.truncated ? <TruncationNotice dimension={selected} /> : null}

      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-line border-b font-mono text-2xs text-ink-dim uppercase tracking-caps">
              <th scope="col" className="py-1.5 pr-3 font-normal">
                {DIMENSION_LABELS[selected]}
              </th>
              {BREAKDOWN_COLUMNS.map((column) => (
                <th
                  key={column.name}
                  scope="col"
                  className={`py-1.5 pl-3 text-right font-normal ${
                    column.core ? "" : "hidden md:table-cell"
                  }`}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <BreakdownRow key={group.key} group={group} />
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-2 text-ink-dim text-xs">
        A dash is a percentile fewer than {MIN_READINGS} readings could not support, never a zero —
        splitting the readings makes every count smaller than the one on the card.
        {selected === "page" ? (
          <>
            {" "}
            A{" "}
            <span className="font-mono text-2xs uppercase tracking-caps">{ROUTE_TEMPLATE_TAG}</span>{" "}
            row is a page you gave the SDK a template for; the rest are single URLs exactly as the
            browser sent them.
          </>
        ) : null}
      </p>
    </section>
  );
}

function DimensionBar({
  breakdowns,
  offered,
  selected,
  onSelect,
}: {
  breakdowns: Breakdowns;
  offered: readonly BreakdownDimension[];
  selected: BreakdownDimension;
  onSelect: (dimension: BreakdownDimension) => void;
}) {
  return (
    <div className="mt-2 flex items-baseline gap-1.5 font-mono text-2xs uppercase tracking-caps">
      <span className="text-ink-dim">Split by</span>
      {offered.map((dimension) => (
        <button
          key={dimension}
          type="button"
          aria-pressed={selected === dimension}
          onClick={() => onSelect(dimension)}
          className={`rounded-sm border px-1.5 py-0.5 ${
            selected === dimension
              ? "border-line bg-raised text-ink"
              : "border-transparent text-ink-dim hover:text-ink"
          }`}
        >
          {DIMENSION_LABELS[dimension]} {breakdowns[dimension]?.groups.length ?? 0}
        </button>
      ))}
    </div>
  );
}

/**
 * Said rather than silently cut, because a page with no route template is grouped by the
 * path exactly as the browser sent it: a site with an order id in its URLs has one row
 * per order, and the busiest pages are the ones that survive. The SDK's `route` option
 * is what collapses them, which is why the notice names it.
 */
function TruncationNotice({ dimension }: { dimension: BreakdownDimension }) {
  return (
    <p className="mt-2 border-warning-border border-l-2 px-3 py-1.5 text-sm text-warning-text">
      These are the busiest {dimension === "page" ? "pages" : "device classes"}, not all of them —
      the read hit its group ceiling.
      {dimension === "page"
        ? " Untagged rows are raw URLs; set the SDK's route option to group them into pages."
        : null}
    </p>
  );
}

function BreakdownRow({ group }: { group: ReadableGroup }) {
  return (
    <tr className="border-line-subtle border-b">
      {/* `w-full max-w-0` is what lets the inner span truncate inside a table cell:
          without it the column simply grows to fit the longest path. */}
      <th scope="row" className="w-full max-w-0 py-1.5 pr-3 font-normal">
        <span className="flex min-w-0 items-baseline gap-1.5">
          {group.isRouteTemplate ? (
            <span
              className="shrink-0 rounded-sm border border-line px-1 font-mono text-2xs text-ink-dim uppercase tracking-caps"
              title="A route template you configured, so every URL matching it is one page."
            >
              {ROUTE_TEMPLATE_TAG}
            </span>
          ) : null}
          <span
            className={`min-w-0 truncate text-sm ${
              group.key === "" ? "text-ink-dim italic" : "text-ink"
            }`}
            title={group.label}
          >
            {group.label}
          </span>
        </span>
      </th>
      {group.cells.map((cell) => (
        <BreakdownValue key={cell.name} cell={cell} />
      ))}
    </tr>
  );
}

function BreakdownValue({ cell }: { cell: BreakdownCell }) {
  return (
    <td
      className={`py-1.5 pl-3 text-right font-mono text-2xs tabular-nums ${
        cell.core ? "" : "hidden md:table-cell"
      } ${RATING_STYLES[cell.rating]}`}
      title={`${cell.label} — ${cell.detail}`}
    >
      {cell.display}
    </td>
  );
}

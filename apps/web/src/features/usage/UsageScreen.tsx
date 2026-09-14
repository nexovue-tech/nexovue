import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef, useState } from "react";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatCount } from "@/shared/format/number";
import { useProjects } from "@/shared/projects";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import type { BillingPeriod, CardinalityWarning, PlanPrice, UsageResponse } from "./api";
import { useUsage } from "./api";
import {
  CARDINALITY_REMEDY,
  DAY_TABLE_CAPTION,
  describeAllowance,
  describeAllowanceSpent,
  describeCeiling,
  describeCostBasis,
  describeCounting,
  describeFamily,
  describeNoPrice,
  describeOverage,
  describePeriodProgress,
  describeRateStart,
  describeScope,
  describeServiceChange,
  formatDay,
  formatPeriod,
  formatPeriodCost,
  formatRate,
  isAllowanceSpent,
  NO_COST_LABEL,
  NO_COST_REASON,
  NO_PRICE_LABEL,
  NOTHING_IS_CAPPED,
  OWN_PERIOD_NOTE,
  STILL_COUNTING,
  UNION_CAPTION,
} from "./usage-format";
import type { DayRange } from "./usage-range";
import { DEFAULT_RANGE_DAYS, RANGE_OPTIONS, usageRange } from "./usage-range";
import type { UsageRow } from "./usage-rows";
import { buildUsageRows, projectLabel, ROW_HEIGHT, unsettledDayCount } from "./usage-rows";

const OVERSCAN_ROWS = 10;

/* Column widths, declared once so the header and every row cannot drift apart.
   `TABLE_WIDTH` is what they add up to with room for the project name: the rows are
   virtualized at a fixed height, so a column that runs out of room cannot wrap, and
   squeezing left the project as "P…" — a row naming neither the project nor, so,
   what its numbers are about. The table scrolls sideways instead. */
const TABLE_WIDTH = "min-w-[32rem]";
const PROJECT_COLUMN = "min-w-0 flex-1 shrink-0 basis-48";
const DAY_COLUMN = "w-24 shrink-0";
const SERVICES_COLUMN = "w-16 shrink-0 text-right";
const COUNT_COLUMN = "w-24 shrink-0 text-right";
const BYTES_COLUMN = "w-20 shrink-0 text-right";

/* **Measured against the table's own width, not the window's.** This was `lg:`, a
   viewport breakpoint — but the table sits inside a sidebar, a settings rail and a
   centred column, so on a 1498px window its pane is under 600px. Every viewport
   breakpoint reported "plenty of room" and the last column was cut off. A container
   query asks the element that actually constrains it. */
const WIDE_ONLY = "hidden @2xl:block";

/**
 * What this organization has ingested, and the services it is billed for.
 *
 * The route component owns the range and the queries; everything visible is in
 * `UsageView`, which is a function of its props and nothing else.
 */
export function UsageScreen() {
  const [rangeDays, setRangeDays] = useState(DEFAULT_RANGE_DAYS);
  // Recomputed each render, which is free: the query key is the two day strings, and
  // those only change when the range does or when the UTC day rolls over.
  const range = usageRange(rangeDays);

  const usage = useUsage(range);
  const projects = useProjects();
  const projectNames = new Map((projects.data ?? []).map((project) => [project.id, project.name]));

  return (
    <SettingsLayout title="Usage">
      <UsageView
        range={range}
        onRangeChange={setRangeDays}
        usage={usage.data}
        isPending={usage.isPending}
        isError={usage.isError}
        error={usage.error}
        onRetry={() => void usage.refetch()}
        projectNames={projectNames}
      />
    </SettingsLayout>
  );
}

interface UsageViewProps {
  range: DayRange;
  onRangeChange: (days: number) => void;
  usage?: UsageResponse;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  projectNames: ReadonlyMap<string, string>;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function UsageView({
  range,
  onRangeChange,
  usage,
  isPending,
  isError,
  error,
  onRetry,
  projectNames,
}: UsageViewProps) {
  return (
    <div className="flex min-h-0 flex-col">
      {/* Both sentences qualify the numbers below rather than name the screen, so
          they stay with the numbers and not in the layout's heading. */}
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <div>
          <p className="text-ink-dim text-sm">{describeScope(range.label)}</p>
          <p className="mt-0.5 text-ink-dim text-xs">{OWN_PERIOD_NOTE}</p>
        </div>
        <RangeToggle days={range.days} onRangeChange={onRangeChange} />
      </div>

      {isPending ? (
        <SkeletonRows rows={10} label="Loading usage" />
      ) : isError || usage === undefined ? (
        <InlineFailure title="Usage could not be loaded" error={error} onRetry={onRetry} />
      ) : (
        <>
          <Summary usage={usage} />
          <CardinalityNotices warnings={usage.cardinality} projectNames={projectNames} />
          {usage.days.length === 0 ? (
            <EmptyState
              title="Nothing metered in this range"
              detail={`No ingestion has been recorded for any project in the last ${range.label}. If your services are exporting, the meter may not have counted this far yet — days appear here as it reaches them.`}
            />
          ) : (
            <DayTable rows={buildUsageRows(usage.days, projectNames)} />
          )}
        </>
      )}
    </div>
  );
}

/** Which range is in force, written as a sentence rather than dressed as a form. */
function RangeToggle({
  days,
  onRangeChange,
}: {
  days: number;
  onRangeChange: (days: number) => void;
}) {
  return (
    <div className="flex shrink-0 items-baseline gap-1.5 font-mono text-2xs uppercase tracking-caps">
      <span className="text-ink-dim">Period</span>
      {RANGE_OPTIONS.map((option) => (
        <button
          key={option.days}
          type="button"
          aria-pressed={days === option.days}
          onClick={() => onRangeChange(option.days)}
          className={`rounded-sm border px-1.5 py-0.5 ${
            days === option.days
              ? "border-line bg-raised text-ink"
              : "border-transparent text-ink-dim hover:text-ink"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * The four things a customer came for — what this month costs, how many services, at
 * what rate, on what plan — and the volume behind them.
 *
 * The multiplication is done by the API and read here, never recomputed: the quantity
 * it uses is the month's, and the range in the table below is a different number.
 * Everything the figure does not cover is written next to it.
 */
function Summary({ usage }: { usage: UsageResponse }) {
  const unsettledDays = unsettledDayCount(usage.days);
  const allowance = describeAllowance(usage.period);

  return (
    <section className="shrink-0 border-line-subtle border-b px-4 pb-4">
      {/* Two columns, not four. Each of these carries a sentence explaining what its
          figure counts, and at a quarter of this pane those sentences wrapped every
          four words into a column twelve lines deep. */}
      <dl className="grid gap-x-10 gap-y-5 sm:grid-cols-2">
        <div>
          <Term>{formatPeriod(usage.period)}</Term>
          <dd>
            <Cost period={usage.period} planName={usage.plan.name} />
          </dd>
        </div>

        <div>
          <Term>Billable services</Term>
          {/* The term names the figure, so it is not a bare number to a screen reader. */}
          <dd>
            <p className="font-semibold font-serif text-2xl text-ink tracking-tight">
              {formatCount(usage.totals.services)}
            </p>
            <p className="mt-1 text-ink-dim text-sm leading-relaxed">{UNION_CAPTION}</p>
          </dd>
        </div>

        <div>
          <Term>Rate</Term>
          <dd>
            <Rate price={usage.price} planName={usage.plan.name} />
          </dd>
        </div>

        <div>
          <Term>Plan</Term>
          <dd>
            <p className="text-base text-ink">{usage.plan.name}</p>
            <p className="mt-1 text-ink-dim text-sm">
              {formatCount(usage.plan.raw_retention_days)} days of traces and logs ·{" "}
              {formatCount(usage.plan.derived_retention_days)} days of metrics
            </p>
          </dd>
        </div>
      </dl>

      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 border-line-subtle border-t pt-3">
        <Ingested term="Spans" value={formatCount(usage.totals.spans)} />
        <Ingested term="Logs" value={formatCount(usage.totals.logs)} />
        <Ingested term="Metric points" value={formatCount(usage.totals.metric_points)} />
        <Ingested term="Bytes" value={formatDecimalBytes(usage.totals.bytes_ingested)} />
        {allowance ? <Ingested term="Included volume" value={allowance} /> : null}
      </dl>

      {isAllowanceSpent(usage.period) ? (
        <p className="mt-3 border-warning-solid border-l-2 py-0.5 pl-3 text-sm text-warning-text">
          {describeAllowanceSpent(usage.period)}
        </p>
      ) : null}

      <p
        className={`mt-3 border-l-2 py-0.5 pl-3 text-sm ${
          usage.settled
            ? "border-line-subtle text-ink-dim"
            : "border-warning-solid text-warning-text"
        }`}
      >
        {describeCounting(usage.settled, unsettledDays)}
      </p>
    </section>
  );
}

/**
 * What the month's services cost, or the fact that nothing can be said.
 *
 * A month still being metered is marked as one. The alternative — a quiet, small,
 * final-looking number — is the failure this screen exists to avoid, and it is the
 * same rule the day rows already follow.
 */
function Cost({ period, planName }: { period: BillingPeriod; planName: string }) {
  const amount = formatPeriodCost(period);

  if (amount === undefined) {
    return (
      <>
        <p className="text-base text-ink">{NO_COST_LABEL}</p>
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">{NO_COST_REASON}</p>
      </>
    );
  }

  return (
    <>
      {/* **Wrapping, because the badge overflowed onto the next column.** A figure
          and a badge in a flex row that cannot wrap is as wide as both of them, and
          a grid column narrower than that does not clip the overflow — it paints it
          over its neighbour, which is how "₹10,773.00 still metering" came to sit on
          top of the billable-services count. */}
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-semibold font-serif text-2xl text-ink tracking-tight">{amount}</span>
        {period.settled ? null : <StillMetering />}
      </p>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">
        {describeCostBasis(period, planName)} {describePeriodProgress(period)}{" "}
        {describeOverage(period)}
      </p>
    </>
  );
}

/** The month is not over, so neither is the number beside this. */
function StillMetering() {
  return (
    <span className="shrink-0 rounded-sm border border-warning-border bg-warning-subtle px-1.5 py-0.5 font-mono text-2xs text-warning-text uppercase tracking-caps">
      still metering
    </span>
  );
}

/**
 * Projects billing for more services than they run, and why.
 *
 * A warning, never a cap: capping would drop the customer's telemetry, and they
 * would then be debugging an outage with data we threw away. When the API could not
 * name a cause, only the change is shown — an accusation nobody can act on is worse
 * than saying nothing.
 */
function CardinalityNotices({
  warnings,
  projectNames,
}: {
  warnings: readonly CardinalityWarning[];
  projectNames: ReadonlyMap<string, string>;
}) {
  if (warnings.length === 0) return null;

  return (
    <section className="shrink-0 border-line-subtle border-b px-4 py-3">
      <ul className="flex flex-col gap-3">
        {warnings.map((warning) => (
          <li
            key={warning.project_id}
            className="border-warning-solid border-l-2 py-0.5 pl-3 text-sm"
          >
            <p className="text-ink">
              <span className="font-medium">{projectLabel(warning.project_id, projectNames)}</span>{" "}
              <span className="text-ink-dim">{describeServiceChange(warning)}</span>
            </p>
            {warning.families.map((family) => (
              <p key={`${family.stem}:${family.kind}`} className="mt-1 text-warning-text">
                {describeFamily(family)}{" "}
                <span className="font-mono text-2xs">{family.examples.join(", ")}</span>
              </p>
            ))}
            {warning.families.length > 0 ? (
              <p className="mt-1 text-ink-dim leading-relaxed">
                {CARDINALITY_REMEDY} {NOTHING_IS_CAPPED}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Term({ children }: { children: string }) {
  return (
    <dt className="mb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">{children}</dt>
  );
}

function Ingested({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{term}</dt>
      <dd className="font-mono text-ink text-sm">{value}</dd>
    </div>
  );
}

/**
 * A price, or the fact that there is not one.
 *
 * An absent price is words. A zero would read as free, and free is a different claim
 * from unpriced — the table ships empty on purpose.
 */
function Rate({ price, planName }: { price: PlanPrice | null; planName: string }) {
  if (price === null) {
    return (
      <>
        <p className="text-base text-ink">{NO_PRICE_LABEL}</p>
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">{describeNoPrice(planName)}</p>
      </>
    );
  }

  const ceiling = describeCeiling(price);
  return (
    <>
      <p className="text-base text-ink">{formatRate(price)}</p>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">
        {describeRateStart(price)}
        {ceiling ? ` ${ceiling}` : ""}
      </p>
    </>
  );
}

function DayTable({ rows }: { rows: readonly UsageRow[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    getItemKey: (index) => rows[index].key,
  });

  return (
    <>
      <p className="shrink-0 px-4 pt-3 pb-2 text-ink-dim text-sm">{DAY_TABLE_CAPTION}</p>

      <div className="@container flex min-h-0 flex-1 flex-col overflow-x-auto">
        <div
          className={`flex shrink-0 items-center border-line border-y bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps ${TABLE_WIDTH}`}
        >
          <span className={DAY_COLUMN}>Day</span>
          <span className={PROJECT_COLUMN}>Project</span>
          <span className={SERVICES_COLUMN}>Services</span>
          <span className={COUNT_COLUMN}>Spans</span>
          <span className={`${COUNT_COLUMN} ${WIDE_ONLY}`}>Logs</span>
          <span className={`${COUNT_COLUMN} ${WIDE_ONLY}`}>Points</span>
          <span className={BYTES_COLUMN}>Bytes</span>
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div
            className={`relative w-full ${TABLE_WIDTH}`}
            style={{ height: virtualizer.getTotalSize() }}
          >
            {virtualizer.getVirtualItems().map((item) => (
              <div
                key={item.key}
                className="absolute inset-x-0 top-0"
                style={{ height: item.size, transform: `translateY(${item.start}px)` }}
              >
                <DayRow row={rows[item.index]} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

function DayRow({ row }: { row: UsageRow }) {
  const { usage } = row;

  return (
    <div
      className={`flex h-full items-center border-line-subtle border-b px-4 text-sm ${TABLE_WIDTH}`}
    >
      <span className={`${DAY_COLUMN} font-mono text-2xs text-ink-dim`}>
        {formatDay(usage.day)}
      </span>
      <span className={`flex items-baseline gap-2 ${PROJECT_COLUMN}`}>
        <span className="truncate text-ink">{row.projectLabel}</span>
        {usage.settled ? null : <StillCounting />}
      </span>
      <span className={`${SERVICES_COLUMN} font-mono text-ink text-2xs`}>
        {formatCount(usage.services)}
      </span>
      <span className={`${COUNT_COLUMN} font-mono text-2xs text-ink-dim`}>
        {formatCount(usage.spans)}
      </span>
      <span className={`${COUNT_COLUMN} ${WIDE_ONLY} font-mono text-2xs text-ink-dim`}>
        {formatCount(usage.logs)}
      </span>
      <span className={`${COUNT_COLUMN} ${WIDE_ONLY} font-mono text-2xs text-ink-dim`}>
        {formatCount(usage.metric_points)}
      </span>
      <span className={`${BYTES_COLUMN} font-mono text-2xs text-ink-dim`}>
        {formatDecimalBytes(usage.bytes_ingested)}
      </span>
    </div>
  );
}

/** A day still being counted is marked, never quietly shown as a low number. */
function StillCounting() {
  return (
    <span className="shrink-0 rounded-sm border border-warning-border bg-warning-subtle px-1.5 py-0.5 font-mono text-2xs text-warning-text uppercase tracking-caps">
      {STILL_COUNTING}
    </span>
  );
}

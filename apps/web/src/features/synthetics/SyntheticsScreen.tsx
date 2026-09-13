import { useNavigate, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { appliedFilters, FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { filterParams, parseFilterValues, withFilter } from "@/shared/filters";
import { formatDuration } from "@/shared/format/duration";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import type { CheckDefinition, CheckResultSummary, CheckTrendPoint } from "./api";
import { useCheckDefinitions, useCheckResults, useCheckTrend } from "./api";
import type { Urgency } from "./check-health";
import {
  certificateUrgency,
  checkUrgency,
  failedRunCount,
  formatCertificateAge,
  formatUptime,
  liveStateOf,
} from "./check-health";
import { SYNTHETIC_FILTERS } from "./filters";
import { PhaseBreakdownPanel } from "./PhaseBreakdownPanel";

/**
 * What the uptime checks measured.
 *
 * Two reads, because they answer different questions: the definitions say what exists,
 * the readings say what happened. A screen with only the readings cannot tell "you have
 * defined no checks" from "your checks reported nothing in the last fifteen minutes",
 * and those need opposite things done about them.
 *
 * The list is the selector; the phase breakdown under it is the point.
 */

const URGENCY_RULE: Record<Urgency, string> = {
  critical: "border-l-critical-solid",
  warning: "border-l-warning-solid",
  none: "border-l-transparent",
};

const URGENCY_TEXT: Record<Urgency, string> = {
  critical: "text-critical-text",
  warning: "text-warning-text",
  none: "text-ink-dim",
};

export function SyntheticsScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow(rangeId);
  const navigate = useNavigate();

  // The route's own id, not its path: `/synthetics` renders inside the shell.
  const search = useSearch({ from: "/shell/synthetics" });
  const filters = useMemo(() => parseFilterValues(SYNTHETIC_FILTERS, search), [search]);

  // `replace`, not push: the back button belongs to the screen a person came
  // from, not to every control they tried on the way.
  const show = (next: Record<string, string | undefined>) =>
    void navigate({ to: "/synthetics", search: () => filterParams(next), replace: true });

  const definitions = useCheckDefinitions(projectId);
  const results = useCheckResults(projectId, timeWindow, filterParams(filters));
  const trendByCheck = useCheckTrend(projectId, timeWindow);
  const [chosenCheckId, setChosenCheckId] = useState<string>();

  const checks = results.data ?? [];
  const narrowed = appliedFilters(SYNTHETIC_FILTERS, filters).length > 0;
  const selected = checks.find((check) => check.check_id === chosenCheckId) ?? checks[0];
  const targets = new Map((definitions.data ?? []).map((check) => [check.id, check]));

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Synthetics</h1>
        <p className="mt-1 text-ink-dim text-sm">
          What we probed for you in the last {timeRangeById(rangeId).label}: whether it answered,
          how often, and which part of the request the time went to.
        </p>
      </header>

      <FilterBar
        definitions={SYNTHETIC_FILTERS}
        values={filters}
        onChange={(key, value) => show(withFilter(filters, key, value))}
        onClearAll={() => show({})}
      />

      {definitions.isPending || results.isPending ? (
        <div className="px-4">
          <SkeletonRows rows={6} label="Loading checks" />
        </div>
      ) : definitions.isError || results.isError ? (
        <div className="px-4">
          <InlineFailure
            title="Checks could not be loaded"
            error={definitions.error ?? results.error}
            onRetry={() => {
              if (definitions.isError) definitions.refetch();
              if (results.isError) results.refetch();
            }}
          />
        </div>
      ) : (definitions.data?.length ?? 0) === 0 ? (
        <EmptyState
          title="No checks are defined yet"
          detail="Nothing is being probed for this project. Checks are created through the API — POST a target URL to /api/v1/checks and the prober picks it up on its next interval."
        />
      ) : checks.length === 0 ? (
        // "Nothing is down" and "the prober is not running" are opposite results,
        // and one sentence for both reports a healthy site as a broken pipeline.
        narrowed ? (
          <EmptyState
            title="No checks match these filters"
            detail="Probes ran in the selected period, but none of them match what you asked for. If you asked for what is down, nothing is."
            action={
              <Button variant="secondary" onClick={() => show({})}>
                Show every check
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No probes in this window"
            detail="The checks exist but published nothing for the selected period. Widen the time range, or check that the prober is running."
          />
        )
      ) : (
        <>
          <CheckTable
            trendByCheck={trendByCheck}
            checks={checks}
            targets={targets}
            selectedCheckId={selected?.check_id}
            onSelect={setChosenCheckId}
          />
          {selected ? (
            <PhaseBreakdownPanel projectId={projectId} check={selected} timeWindow={timeWindow} />
          ) : null}
        </>
      )}
    </div>
  );
}

/* 104px of sparkline plus its 12px gutter, reserved on every row. */
const TREND_COLUMN = "w-29 shrink-0 pl-3";

/**
 * One scale for the screen, in milliseconds, so two checks are comparable.
 *
 * Buckets with no successful run carry no duration and contribute nothing to the
 * ceiling — a failure must not raise or lower the scale the successes are drawn against.
 */
export function trendCeiling(
  checks: readonly CheckResultSummary[],
  trendByCheck: ReadonlyMap<string, readonly CheckTrendPoint[]> | undefined,
): number {
  let ceiling = 0;
  for (const check of checks) {
    for (const point of trendByCheck?.get(check.check_id) ?? []) {
      if (point.mean_duration_ms !== null && point.mean_duration_ms > ceiling) {
        ceiling = point.mean_duration_ms;
      }
    }
  }
  return ceiling;
}

function CheckTable({
  checks,
  targets,
  selectedCheckId,
  onSelect,
  trendByCheck,
}: {
  checks: readonly CheckResultSummary[];
  targets: Map<string, CheckDefinition>;
  selectedCheckId: string | undefined;
  onSelect: (checkId: string) => void;
  trendByCheck?: ReadonlyMap<string, readonly CheckTrendPoint[]>;
}) {
  const ceiling = trendCeiling(checks, trendByCheck);

  return (
    <div className="shrink-0">
      <div className="flex items-center border-line border-b bg-surface px-4 py-1.5 pl-6 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="min-w-0 flex-1">Check</span>
        <span className="w-20 text-right">Uptime</span>
        <span className="hidden w-16 text-right sm:block">Runs</span>
        <span className="w-20 text-right">Mean</span>
        <span className="w-20 text-right">p95</span>
        <span className="hidden w-20 text-right md:block">Max</span>
        <span className="hidden w-40 text-right lg:block">Certificate</span>
        <span className={TREND_COLUMN}>Trend</span>
      </div>

      {checks.map((check) => (
        <CheckRow
          key={check.check_id}
          check={check}
          definition={targets.get(check.check_id)}
          selected={check.check_id === selectedCheckId}
          onSelect={onSelect}
          trend={trendByCheck?.get(check.check_id)}
          ceiling={ceiling}
        />
      ))}
    </div>
  );
}

function CheckRow({
  check,
  definition,
  selected,
  onSelect,
  trend,
  ceiling,
}: {
  check: CheckResultSummary;
  definition: CheckDefinition | undefined;
  selected: boolean;
  onSelect: (checkId: string) => void;
  trend?: readonly CheckTrendPoint[];
  ceiling: number;
}) {
  const urgency = checkUrgency(check);

  return (
    <button
      type="button"
      aria-current={selected ? "true" : undefined}
      onClick={() => onSelect(check.check_id)}
      className={`flex w-full items-center border-line-subtle border-b border-l-2 px-4 py-2 text-left text-sm hover:bg-hover ${
        URGENCY_RULE[urgency]
      } ${selected ? "bg-raised" : ""}`}
    >
      <span className="flex min-w-0 flex-1 items-baseline gap-2">
        <LiveState check={check} />
        <span className="truncate text-ink">{identityOf(check, definition)}</span>
        {definition && !definition.enabled ? (
          <span className="shrink-0 font-mono text-2xs text-ink-dim">paused</span>
        ) : null}
      </span>

      <span className={`w-20 text-right font-mono text-2xs ${URGENCY_TEXT[uptimeTone(check)]}`}>
        {formatUptime(check)}
      </span>

      <span className="hidden w-16 text-right font-mono text-2xs text-ink-dim sm:block">
        {check.runs}
      </span>

      <span className="w-20 text-right font-mono text-2xs text-ink-dim">
        {formatDuration(check.mean_duration_ms)}
      </span>

      <span className="w-20 text-right font-mono text-2xs text-ink">
        {formatDuration(check.p95_duration_ms)}
      </span>

      <span className="hidden w-20 text-right font-mono text-2xs text-ink-dim md:block">
        {formatDuration(check.max_duration_ms)}
      </span>

      <span className="hidden w-40 truncate text-right font-mono text-2xs lg:block">
        <Certificate check={check} />
      </span>

      <span className={TREND_COLUMN}>
        {/* A bucket nothing succeeded in has no duration, and the line breaks there. A
            zero would be drawn on the floor and read as the site getting instantly
            fast at the moment it went down. */}
        <Sparkline
          points={(trend ?? []).map((point) => point.mean_duration_ms)}
          max={ceiling}
          absent="gap"
          label={`${identityOf(check, definition)} response time over the window`}
        />
      </span>
    </button>
  );
}

/**
 * Up carries no colour, by the rule that health is the absence of it. Down is the one
 * state on this screen that gets a filled mark, and it also gets the word.
 */
function LiveState({ check }: { check: CheckResultSummary }) {
  const state = liveStateOf(check);

  if (state === "down") {
    return (
      <span className="flex shrink-0 items-baseline gap-1.5 font-mono text-2xs text-critical-text">
        <span aria-hidden="true" className="h-1.5 w-1.5 translate-y-px bg-critical-solid" />
        down
        {check.latest_status_code > 0 ? ` ${check.latest_status_code}` : ""}
      </span>
    );
  }

  const label = state === "up" ? `up ${check.latest_status_code}` : "no data";
  return (
    <span
      className="shrink-0 font-mono text-2xs text-ink-dim"
      title={check.latest_run_at ? `Last probe ${formatTimestamp(check.latest_run_at)}` : undefined}
    >
      {label}
      {check.latest_run_at ? ` · ${formatRelativeTime(check.latest_run_at)}` : ""}
    </span>
  );
}

/** A certificate about to expire is an outage nobody has had yet, so it earns colour. */
function Certificate({ check }: { check: CheckResultSummary }) {
  if (!check.tls) return <span className="text-ink-dim">—</span>;

  const urgency = certificateUrgency(check.tls.days_remaining);
  return (
    <span className={URGENCY_TEXT[urgency]} title={`Issued by ${check.tls.issuer}`}>
      {formatCertificateAge(check.tls.days_remaining)}
    </span>
  );
}

/** The uptime number itself is only coloured when something in the window failed. */
function uptimeTone(check: CheckResultSummary): Urgency {
  if (liveStateOf(check) === "down") return "critical";
  return failedRunCount(check) > 0 ? "warning" : "none";
}

function identityOf(check: CheckResultSummary, definition: CheckDefinition | undefined): string {
  if (definition) return definition.target;
  if (check.tls) return check.tls.server_address;
  // A reading whose definition has been deleted still has readings worth showing.
  return check.check_id;
}

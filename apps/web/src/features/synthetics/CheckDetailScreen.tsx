import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { isNotFound } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Tabs } from "@/shared/components/Tabs";
import { formatDuration } from "@/shared/format/duration";
import { plural } from "@/shared/format/plural";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import type { TimeRange, TimeWindow } from "@/shared/selection";
import {
  setSelectedRangeId,
  TIME_RANGES,
  useLiveWindow,
  useSelection,
  useWindowPhrase,
} from "@/shared/selection";

import type { CheckDefinition, CheckRun } from "./api";
import {
  useCheck,
  useCheckResults,
  useCheckRuns,
  useDeleteCheck,
  useDeletionImpact,
  useIssueHeartbeatToken,
  useRunEvidence,
  useStateChanges,
  useUpdateCheck,
} from "./api";
import { CheckForm } from "./CheckForm";
import { draftFrom } from "./check-draft";
import { formatUptime } from "./check-health";
import { EvidenceView } from "./EvidenceView";
import { KIND_DESCRIPTIONS } from "./NewCheckScreen";
import { isOutcome, OUTCOME_WORDS, outcomeUrgency, STATE_WORDS, stateUrgency } from "./outcomes";
import { PhaseBreakdownPanel } from "./PhaseBreakdownPanel";

type CheckTab = "overview" | "edit";

export function parseCheckSearch(search: Record<string, unknown>): {
  tab?: CheckTab;
  run?: string;
} {
  return {
    ...(search.tab === "edit" ? { tab: "edit" as const } : {}),
    ...(typeof search.run === "string" && search.run ? { run: search.run } : {}),
  };
}

const URGENCY_TEXT = {
  critical: "font-medium text-critical-text",
  warning: "font-medium text-warning-text",
  none: "text-ink",
} as const;

/** A check that has run nothing for this many intervals is overdue, not quiet. */
const OVERDUE_INTERVALS = 3;
const MS_PER_MINUTE = 60_000;

/**
 * One check: its state and since when, whether it stayed up, and — when it failed —
 * the request that failed (design, *User experience → Diagnose*). The developer should
 * never have to go to Logs to find out why.
 */
export function CheckDetailScreen() {
  const { checkId } = useParams({ from: "/shell/synthetics/$checkId" });
  const search = useSearch({ from: "/shell/synthetics/$checkId" });
  const navigate = useNavigate();
  const check = useCheck(checkId);
  const tab: CheckTab = search.tab ?? "overview";

  if (check.isPending) {
    return (
      <div className="px-4 pt-5">
        <SkeletonRows rows={6} label="Loading the check" />
      </div>
    );
  }
  if (check.isError) {
    return isNotFound(check.error) ? (
      <EmptyState
        title="No such check"
        detail="It may have been deleted, or it belongs to another organization."
        action={
          <Link to="/synthetics" className="text-brand-text text-sm hover:underline">
            Back to every check
          </Link>
        }
      />
    ) : (
      <div className="px-4 pt-5">
        <InlineFailure
          title="The check could not be loaded"
          error={check.error}
          onRetry={() => check.refetch()}
        />
      </div>
    );
  }

  const definition = check.data;
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <CheckHeader check={definition} />
      <div className="min-w-0 px-4 pb-10">
        <Tabs
          label="Check"
          items={[
            { id: "overview", label: "Overview" },
            { id: "edit", label: "Settings" },
          ]}
          selected={tab}
          onSelect={(id) =>
            void navigate({
              to: "/synthetics/$checkId",
              params: { checkId },
              search: id === "edit" ? { tab: "edit" } : {},
              replace: true,
            })
          }
        >
          <div className="pt-4">
            {tab === "overview" ? (
              <Overview check={definition} selectedRun={search.run} />
            ) : (
              <EditCheck check={definition} />
            )}
          </div>
        </Tabs>
      </div>
    </div>
  );
}

function CheckHeader({ check }: { check: CheckDefinition }) {
  const { projectId } = useSelection();
  const update = useUpdateCheck(check.id, projectId);
  const [changed, setChanged] = useState<"Paused." | "Resumed.">();
  const state = check.displayed_state;

  return (
    <header className="shrink-0 px-4 pt-5 pb-3">
      <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <Link to="/synthetics" className="hover:text-ink">
          Synthetics
        </Link>{" "}
        / {KIND_DESCRIPTIONS[check.type].title}
      </p>
      <div className="mt-1 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="min-w-0 break-words font-semibold font-serif text-ink text-xl tracking-tight">
          {check.name}
        </h1>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            pending={update.isPending}
            pendingLabel={check.enabled ? "Pausing" : "Resuming"}
            onClick={() => {
              setChanged(undefined);
              update.mutate(
                { enabled: !check.enabled },
                { onSuccess: (saved) => setChanged(saved.enabled ? "Resumed." : "Paused.") },
              );
            }}
          >
            {check.enabled ? "Pause" : "Resume"}
          </Button>
          {changed ? (
            <span role="status" className="self-center text-ink-dim text-sm">
              {changed}
            </span>
          ) : null}
        </div>
      </div>
      <p className="mt-1 text-sm">
        {state ? (
          <span className={URGENCY_TEXT[stateUrgency(state)]}>{STATE_WORDS[state]}</span>
        ) : (
          <span className="text-ink-dim">not judged yet</span>
        )}
        {check.state_since && state !== "paused" && state !== "maintenance" ? (
          <span className="text-ink-dim" title={formatTimestamp(check.state_since)}>
            {" "}
            since {formatRelativeTime(check.state_since)}
          </span>
        ) : null}
        <span className="text-ink-dim"> · {describeSchedule(check)}</span>
      </p>
      {update.error ? (
        <InlineFailure title="The check could not be changed" error={update.error} />
      ) : null}
    </header>
  );
}

function describeSchedule(check: CheckDefinition): string {
  const every = describeInterval(check.interval_seconds);
  if (check.type === "heartbeat") {
    const last = check.last_ping_at
      ? `last called ${formatRelativeTime(check.last_ping_at)}`
      : "never called";
    // Paused, nothing is expected of the job: the sweep is not judging its silence.
    return `${check.enabled ? "expected" : "was expected"} ${every} · ${last}`;
  }
  const where = check.locations.length ? check.locations.join(", ") : "every location";
  // A paused check is not on a schedule. Saying it "runs every minute" beside the
  // word "paused" contradicts the state next to it.
  if (!check.enabled) return `would run ${every} from ${where}`;
  return `runs ${every} from ${where}`;
}

function describeInterval(seconds: number): string {
  const span = describeSpan(seconds);
  return `every ${span.replace(/^one /, "")}`;
}

/** "one minute", "5 minutes", "one hour", "90 seconds". */
function describeSpan(seconds: number): string {
  if (seconds % 3600 === 0) return seconds === 3600 ? "one hour" : `${seconds / 3600} hours`;
  if (seconds % 60 === 0) return seconds === 60 ? "one minute" : `${seconds / 60} minutes`;
  return `${seconds} seconds`;
}

function Overview({
  check,
  selectedRun,
}: {
  check: CheckDefinition;
  selectedRun: string | undefined;
}) {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();
  const results = useCheckResults(projectId, timeWindow);
  const summary = results.data?.find((result) => result.check_id === check.id);

  const silence = whySilent(check, { ...timeWindow, phrase: windowPhrase });
  const widenTo = silence.widenTo;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      {check.type === "heartbeat" ? <HeartbeatUrl check={check} /> : null}

      <section aria-label="Readings" className="flex flex-col gap-2">
        {results.isPending ? (
          <SkeletonRows rows={1} label="Loading readings" />
        ) : results.isError ? (
          <InlineFailure
            title="Readings could not be loaded"
            error={results.error}
            onRetry={() => results.refetch()}
          />
        ) : summary ? (
          <dl className="grid max-w-2xl grid-cols-2 gap-3 sm:grid-cols-4">
            <Reading label={`Uptime, ${windowPhrase}`} value={formatUptime(summary)} />
            <Reading label="Runs counted" value={String(summary.runs)} />
            <Reading label="p95" value={formatDuration(summary.p95_duration_ms)} />
            <Reading
              label="Certificate"
              value={summary.tls ? `${Math.floor(summary.tls.days_remaining)} days` : "—"}
            />
          </dl>
        ) : (
          <EmptyState
            title={silence.title}
            detail={silence.detail}
            action={
              widenTo ? (
                <Button variant="secondary" onClick={() => setSelectedRangeId(widenTo.id)}>
                  {`Show the last ${widenTo.label}`}
                </Button>
              ) : undefined
            }
          />
        )}
      </section>

      {summary && check.type !== "heartbeat" && check.type !== "dns" ? (
        <PhaseBreakdownPanel projectId={projectId} check={summary} timeWindow={timeWindow} />
      ) : null}

      <RunsAndEvidence check={check} timeWindow={timeWindow} selectedRun={selectedRun} />
      <StateTimeline check={check} />
      <DeleteCheck check={check} />
    </div>
  );
}

function Reading({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{label}</dt>
      <dd className="font-mono text-ink text-lg">{value}</dd>
    </div>
  );
}

interface Silence {
  title: string;
  detail: string;
  /** The shortest range that reaches the last run, when the window misses it. */
  widenTo?: TimeRange;
}

/**
 * EC11: nothing to show is always one of four things, and says which — or it has run,
 * and the window is what misses it.
 */
export function whySilent(
  check: Pick<
    CheckDefinition,
    "enabled" | "in_maintenance" | "last_run_at" | "interval_seconds" | "created_at" | "type"
  >,
  shown: TimeWindow & { phrase: string },
  now = Date.now(),
): Silence {
  // A heartbeat is judged on silence, never probed, so every sentence below has to
  // be able to say "called" where it would say "ran". One word, threaded through.
  const called = check.type === "heartbeat";
  if (!check.enabled) {
    return {
      title: "Paused",
      detail: called
        ? "This check is paused, so calls to its URL are not being judged. Resume it to start again."
        : "This check is paused, so nothing is being run. Resume it to start again.",
    };
  }
  if (check.in_maintenance) {
    return {
      title: "In maintenance",
      detail:
        "A maintenance window is open. Runs continue and are marked; none changes the check's state.",
    };
  }
  const since = Date.parse(check.last_run_at ?? check.created_at);
  if (now - since > OVERDUE_INTERVALS * check.interval_seconds * 1000) {
    return {
      title: check.last_run_at
        ? "Overdue"
        : `${called ? "Not called" : "Not run"} yet — and overdue`,
      detail: called
        ? "Nothing has been recorded for several periods. Either your job has stopped calling, or the sweep that judges heartbeats is not running."
        : "Nothing has been recorded for several intervals. The probe workers may be stopped; runs resume on their own once they are back.",
    };
  }
  if (check.last_run_at) {
    const lastRun = Date.parse(check.last_run_at);
    const when = formatRelativeTime(check.last_run_at, now);
    const ran = called ? `It was last judged ${when}` : `It last ran ${when}`;
    const nothing = `No ${called ? "calls" : "runs"} in ${shown.phrase}`;
    if (lastRun >= Date.parse(shown.from) && lastRun <= Date.parse(shown.to)) {
      return {
        title: nothing,
        detail: `${ran}, and its reading has not arrived yet. This page fills in as it lands.`,
      };
    }
    const widenTo = TIME_RANGES.find((range) => range.minutes * MS_PER_MINUTE >= now - lastRun);
    return {
      title: nothing,
      detail: widenTo
        ? `${ran}, outside this window.`
        : `${ran}, outside this window. Choose a window that includes it from the time control at the top of the page.`,
      widenTo,
    };
  }
  if (called) {
    return {
      title: "Not called yet",
      detail:
        "A heartbeat is not probed: this fills in when your job calls its URL. Until then there is nothing to judge.",
    };
  }
  return {
    title: "Not run yet",
    detail: `The first run is due within ${describeSpan(check.interval_seconds)} of saving. This page fills in as runs land.`,
  };
}

/**
 * Why the list is empty. Widening the window only helps when there is something
 * older to find; telling someone to widen it for a check that has never run sends
 * them looking for history that does not exist.
 */
function noRunsSentence(check: CheckDefinition): string {
  const called = check.type === "heartbeat";
  if (!check.last_run_at) {
    return called
      ? "Nothing has called this heartbeat's URL yet. Its calls appear here."
      : "This check has not run yet. Its runs appear here as they land.";
  }
  return called
    ? "No calls in this window. Widen the time range to see older ones."
    : "No runs in this window. Widen the time range to see older ones.";
}

function RunsAndEvidence({
  check,
  timeWindow,
  selectedRun,
}: {
  check: CheckDefinition;
  timeWindow: TimeWindow;
  selectedRun: string | undefined;
}) {
  const { projectId } = useSelection();
  const navigate = useNavigate();
  const runs = useCheckRuns(projectId, check.id, timeWindow);
  const list = runs.data ?? [];
  const chosen =
    selectedRun ??
    list.find((run) => run.outcome !== "up" && run.run_id)?.run_id ??
    list[0]?.run_id;
  const evidence = useRunEvidence(projectId, check.id, chosen, timeWindow);

  return (
    <section aria-label="Runs" className="flex min-w-0 flex-col gap-3">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">Runs</h2>
      {runs.isPending ? (
        <SkeletonRows rows={4} label="Loading runs" />
      ) : runs.isError ? (
        <InlineFailure
          title="Runs could not be loaded"
          error={runs.error}
          onRetry={() => runs.refetch()}
        />
      ) : list.length === 0 ? (
        <p className="text-ink-dim text-sm">{noRunsSentence(check)}</p>
      ) : (
        <div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
          <ol className="flex max-h-[28rem] flex-col overflow-y-auto border-line-subtle border-t">
            {list.map((run) => (
              <RunRow
                key={`${run.timestamp}-${run.run_id}`}
                run={run}
                selected={run.run_id !== "" && run.run_id === chosen}
                onSelect={() =>
                  void navigate({
                    to: "/synthetics/$checkId",
                    params: { checkId: check.id },
                    search: { run: run.run_id },
                    replace: true,
                  })
                }
              />
            ))}
          </ol>
          <div className="min-w-0">
            {!chosen ? (
              <p className="text-ink-dim text-sm">
                These runs were recorded before evidence was kept.
              </p>
            ) : evidence.isPending ? (
              <SkeletonRows rows={4} label="Loading what the run saw" />
            ) : evidence.isError ? (
              <InlineFailure title="What this run saw could not be loaded" error={evidence.error} />
            ) : (
              <EvidenceView key={evidence.data.run_id} evidence={evidence.data} />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: CheckRun;
  selected: boolean;
  onSelect: () => void;
}) {
  const word = isOutcome(run.outcome) ? OUTCOME_WORDS[run.outcome] : run.up ? "passed" : "failed";
  return (
    <li>
      <button
        type="button"
        disabled={!run.run_id}
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        className={`flex w-full items-baseline gap-2 border-line-subtle border-b px-2 py-1.5 text-left text-sm hover:bg-hover disabled:cursor-default ${
          selected ? "bg-raised" : ""
        }`}
      >
        <span
          className="w-20 shrink-0 font-mono text-2xs text-ink-dim"
          title={formatTimestamp(run.timestamp)}
        >
          {formatRelativeTime(run.timestamp)}
        </span>
        <span className={`min-w-0 flex-1 truncate ${URGENCY_TEXT[outcomeUrgency(run.outcome)]}`}>
          {word}
          {!run.final ? <span className="font-normal text-ink-dim"> · retried</span> : null}
          {run.maintenance ? (
            <span className="font-normal text-ink-dim"> · maintenance</span>
          ) : null}
        </span>
        <span className="hidden font-mono text-2xs text-ink-dim sm:inline">{run.location}</span>
        <span className="w-16 shrink-0 text-right font-mono text-2xs text-ink-dim">
          {run.status_code > 0 ? run.status_code : "—"}
        </span>
        <span className="w-16 shrink-0 text-right font-mono text-2xs text-ink">
          {formatDuration(run.duration_ms)}
        </span>
      </button>
    </li>
  );
}

function StateTimeline({ check }: { check: CheckDefinition }) {
  const changes = useStateChanges(check.id);
  return (
    <section aria-label="State changes" className="flex flex-col gap-2">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">State changes</h2>
      {changes.isPending ? (
        <SkeletonRows rows={2} label="Loading state changes" />
      ) : changes.isError ? (
        <InlineFailure title="State changes could not be loaded" error={changes.error} />
      ) : (changes.data?.length ?? 0) === 0 ? (
        <p className="text-ink-dim text-sm">
          No confirmed change yet. A single failure never changes a state on its own.
        </p>
      ) : (
        <ol className="flex flex-col gap-1">
          {changes.data?.map((change) => (
            <li
              key={`${change.changed_at}-${change.to_state}`}
              className="flex flex-wrap items-baseline gap-2 text-sm"
            >
              <span
                className="w-28 shrink-0 font-mono text-2xs text-ink-dim"
                title={formatTimestamp(change.changed_at)}
              >
                {formatRelativeTime(change.changed_at)}
              </span>
              <span className="text-ink">
                {change.from_state ? `${change.from_state} → ` : ""}
                <span className={URGENCY_TEXT[outcomeUrgency(change.to_state)]}>
                  {change.to_state}
                </span>
              </span>
              {change.location ? (
                <span className="font-mono text-2xs text-ink-dim">{change.location}</span>
              ) : null}
              {change.reason ? (
                <span className="min-w-0 basis-full text-ink-dim text-xs sm:basis-auto">
                  {change.reason}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function HeartbeatUrl({ check }: { check: CheckDefinition }) {
  const issue = useIssueHeartbeatToken(check.id);
  const [confirming, setConfirming] = useState(false);
  const url = issue.data ? `${window.location.origin}${issue.data.ping_path}` : undefined;
  return (
    <section aria-label="Heartbeat URL" className="flex max-w-2xl flex-col gap-2">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">Heartbeat URL</h2>
      {url ? (
        <>
          <p className="text-ink text-sm">
            <strong>Copy it now — it is not shown again.</strong> The previous URL no longer works.
          </p>
          <div className="flex min-w-0 items-center gap-2 rounded-sm border border-line bg-surface px-2 py-1.5">
            <code className="min-w-0 flex-1 break-all font-mono text-ink text-xs">{url}</code>
            <CopyButton value={url} label="heartbeat URL" />
          </div>
        </>
      ) : (
        <p className="text-ink-dim text-sm">
          The URL was shown once, when the check was created, and only its fingerprint is kept. If
          it is lost, issue a new one — the job must then call the new URL.
        </p>
      )}
      {confirming && !url ? (
        <div className="flex flex-wrap items-center gap-2 border-warning-solid border-l-2 px-3 py-2">
          <p className="text-ink text-sm">
            Issue a new URL? The one your job calls now stops working immediately.
          </p>
          <Button pending={issue.isPending} pendingLabel="Issuing" onClick={() => issue.mutate()}>
            Issue new URL
          </Button>
          <Button variant="secondary" onClick={() => setConfirming(false)}>
            Keep the current one
          </Button>
        </div>
      ) : !url ? (
        <div>
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            Issue a new URL
          </Button>
        </div>
      ) : null}
      {issue.error ? (
        <InlineFailure title="A new URL could not be issued" error={issue.error} />
      ) : null}
    </section>
  );
}

function EditCheck({ check }: { check: CheckDefinition }) {
  const { projectId } = useSelection();
  const update = useUpdateCheck(check.id, projectId);
  const [saved, setSaved] = useState(false);
  const initial = useMemo(() => draftFrom(check), [check]);
  if (projectId === undefined) return <SkeletonRows rows={4} label="Loading the project" />;
  return (
    <div className="flex flex-col gap-4">
      {saved ? (
        <p role="status" className="text-ink text-sm">
          Saved. The next run uses this — within about half a minute of the scheduler seeing it.
        </p>
      ) : null}
      {/* Keyed on the check, not its version: a pause or a refresh must not discard a draft. */}
      <CheckForm
        key={check.id}
        projectId={projectId}
        initial={initial}
        version={check.updated_at}
        submitLabel="Save changes"
        pendingLabel="Saving"
        save={{
          mutate: (input, options) => update.mutate(input, options),
          isPending: update.isPending,
          error: update.error,
          reset: update.reset,
        }}
        onSaved={() => setSaved(true)}
      />
    </div>
  );
}

function DeleteCheck({ check }: { check: CheckDefinition }) {
  const { projectId } = useSelection();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteCheck(projectId);
  const impact = useDeletionImpact(projectId, check, confirming);
  const runs = impact.runs.data?.runs;
  const files = impact.files.data?.files;
  const counted = runs !== undefined && files !== undefined;

  return (
    <section
      aria-label="Delete"
      className="flex max-w-2xl flex-col gap-2 border-line border-t pt-6"
    >
      {confirming ? (
        <div className="flex flex-col gap-2 border-critical-solid border-l-2 px-3 py-2">
          <p className="text-ink text-sm">
            {counted
              ? `Deleting “${check.name}” removes its definition, ${runs} ${plural(runs, "recorded run")} of history and ${files} ${plural(files, "stored file")}. It cannot be undone.`
              : impact.runs.isError || impact.files.isError
                ? `Deleting “${check.name}” removes its definition, its history and its files. The numbers could not be counted just now.`
                : "Counting what would be lost…"}
          </p>
          {check.enabled ? (
            <p className="text-ink-dim text-sm">
              To stop it without losing anything, pause it instead.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              pending={remove.isPending}
              pendingLabel="Deleting"
              disabled={!counted && !(impact.runs.isError || impact.files.isError)}
              onClick={() =>
                remove.mutate(check.id, { onSuccess: () => void navigate({ to: "/synthetics" }) })
              }
            >
              Delete check
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </div>
          {remove.error ? (
            <InlineFailure title="The check could not be deleted" error={remove.error} />
          ) : null}
        </div>
      ) : (
        <div>
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            Delete…
          </Button>
        </div>
      )}
    </section>
  );
}

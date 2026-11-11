import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Sparkline } from "@/shared/components/Sparkline";
import { CHART_HEIGHT, TrendChart } from "@/shared/components/TrendChart";
import { useDetectionSettings } from "@/shared/detection/api";
import { detectionHeadlineFor, headlineFor } from "@/shared/detection/headline";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { formatRatePerMinute } from "@/shared/format/rate";
import { formatRelativeTime } from "@/shared/format/time";
import { formatCalendarDay } from "@/shared/format/zoned";
import { useCan, useSession } from "@/shared/identity";
import type { OverviewPlot, OverviewPoint } from "@/shared/overview-plots";
import { buildOverviewPlots } from "@/shared/overview-plots";
import { useLiveWindow, useSelection } from "@/shared/selection";
import { SETUP_DOCS_PATH, setupDocUrl } from "@/shared/setup-docs";

import type {
  CaseFilter,
  CheckCounts,
  DetectionStatus,
  FeedFinding,
  FindingSeverity,
  ProjectTotals,
} from "./api";
import {
  useCaseFeed,
  useCheckCounts,
  useDetectionStatus,
  useFindings,
  useProjectSummary,
  useResolveFromFeed,
} from "./api";
import { Segments } from "./ExplanationSection";
import type { FeedChoice } from "./feed-filter";
import { ACROSS_PROJECTS, FEED_CHOICES, NO_CASES, parseFeedSearch } from "./feed-filter";
import { caseEndingSentence, caseSentence } from "./finding-page";
import { CLOSED_AS } from "./finding-words";
import { orderFindings } from "./ordering";

/**
 * The feed of things that need you, written as sentences.
 *
 * **The healthy state is the one that matters.** F1 AC1.7 forbids a blank page and a
 * spinner here by name — when nothing is wrong, silence has to read as information
 * rather than as a page that failed.
 *
 * **AC18 is the single most dangerous requirement on this screen.** A quiet feed
 * and "we could not read the feed" must never look alike, because conflating them tells a
 * customer their system is healthy when the truth is that we cannot see it. The two
 * states share no copy, no icon and no layout, and a test asserts they differ.
 *
 * Detection's own line leads the feed (`headline.ts`), and a failed read of it is the
 * screen's error state like any other: a status we could not read must not leave the
 * checks' line standing alone as if nothing had been asked.
 *
 * Muted findings stay out of the feed. A quiet link under it opens them instead.
 *
 * **The case filters are a different read** (F22 FR22, design *The feed*): *Active
 * cases*, *Assigned to me* and *Resolved cases* read every project, muted findings
 * included, with their own query and no telemetry, so a failed summary at 3 a.m. can
 * never hide the case list (AC109). They show no detection line, no summary and no
 * muted link, and the filter lives in the address (AC28).
 */
export function FindingsScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const [showingMuted, setShowingMuted] = useState(false);
  const navigate = useNavigate();
  const caseFilter = parseFeedSearch(useSearch({ strict: false })).case ?? null;
  // The plain feed's reads name the project; a case filter's reads none of them.
  const feedProject = caseFilter === null ? projectId : undefined;

  const feed = useFindings(feedProject, showingMuted, timeWindow);
  const summary = useProjectSummary(feedProject, timeWindow);
  // The checks read waits for the summary: past the telemetry tier's in-flight cap
  // — 4 per process at two API processes — a read waits in the limiter, and this
  // screen must not spend the queue on itself (F20 plan, T1.8).
  const checks = useCheckCounts(feedProject, timeWindow, summary.data !== undefined);
  const detection = useDetectionStatus(feedProject, timeWindow);
  const cases = useCaseFeed(caseFilter, timeWindow);
  // Not waited for: the browser's clock serves until the project's is read.
  const settings = useDetectionSettings(projectId);
  const session = useSession();
  const canResolve = useCan("operate_cases") && !session.data?.support_view;
  const resolve = useResolveFromFeed();

  const choose = (choice: FeedChoice) => {
    void navigate({ to: "/", search: choice === "all" ? {} : { case: choice } });
  };
  const shared = {
    caseFilter,
    onChoose: choose,
    viewerId: session.data?.user.id ?? null,
    canResolve,
    onResolve: (findingId: string) => resolve.mutate(findingId),
    resolvingId: resolve.isPending ? (resolve.variables ?? null) : null,
    resolveError: resolve.error,
    showingMuted,
    onToggleMuted: () => setShowingMuted((showing) => !showing),
  };

  if (caseFilter !== null) {
    return (
      <FindingsView
        {...shared}
        findings={cases.data?.findings ?? []}
        truncated={cases.data?.truncated ?? false}
        mutedCount={0}
        isPending={cases.isPending}
        isError={cases.isError}
        error={cases.error}
        onRetry={() => void cases.refetch()}
      />
    );
  }
  return (
    <FindingsView
      {...shared}
      findings={feed.data?.findings ?? []}
      truncated={feed.data?.truncated ?? false}
      mutedCount={feed.data?.muted_count ?? 0}
      isPending={feed.isPending || summary.isPending || detection.isPending}
      isError={feed.isError || summary.isError || detection.isError}
      error={feed.error ?? summary.error ?? detection.error}
      onRetry={() => {
        void feed.refetch();
        void summary.refetch();
        void detection.refetch();
      }}
      totals={summary.data?.totals}
      points={summary.data?.points}
      checks={checks.data}
      detection={detection.data}
      zone={settings.data?.timezone}
    />
  );
}

export interface FindingsViewProps {
  findings: readonly FeedFinding[];
  /** A case filter (FR22), or null for the plain feed. */
  caseFilter?: CaseFilter | null;
  onChoose?: (choice: FeedChoice) => void;
  /** The reader, so their own case reads "You took this". */
  viewerId?: string | null;
  /** May resolve a case from its row (FR52): a working role, not in a support view. */
  canResolve?: boolean;
  onResolve?: (findingId: string) => void;
  /** The row whose Resolve is in flight. */
  resolvingId?: string | null;
  resolveError?: unknown;
  /** True when the API held more than it sent: the feed says it is the first 200. */
  truncated?: boolean;
  /** How many findings are muted, and so left out of the feed. */
  mutedCount: number;
  /** True when the list holds the muted findings instead of the feed. */
  showingMuted: boolean;
  onToggleMuted: () => void;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  totals?: ProjectTotals;
  /** The buckets behind the totals, from the same answer: the empty feed's charts. */
  points?: readonly OverviewPoint[];
  checks?: CheckCounts;
  detection?: DetectionStatus;
  /** The IANA zone times are read on: the browser's until the project names its own. */
  zone?: string;
}

export function FindingsView({
  findings,
  caseFilter = null,
  onChoose = () => {},
  viewerId = null,
  canResolve = false,
  onResolve = () => {},
  resolvingId = null,
  resolveError,
  truncated = false,
  mutedCount,
  showingMuted,
  onToggleMuted,
  isPending,
  isError,
  error,
  onRetry,
  totals,
  points = [],
  checks,
  detection,
  zone,
}: FindingsViewProps) {
  // Resolved cases arrive newest-resolved first, and keep that order (FR22).
  const ordered = caseFilter === "resolved" ? [...findings] : orderFindings(findings);
  const detectionLine = detectionHeadlineFor(detection, { zone });
  const rowContext: RowContext = {
    caseFilter,
    viewerId,
    zone,
    canResolve: canResolve && (caseFilter === "active" || caseFilter === "mine"),
    onResolve,
    resolvingId,
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Findings</h1>
        <p className="mt-1 max-w-prose text-ink-dim text-sm">
          Things that need you, newest first. Anything still wrong or still being worked comes
          before anything finished.
        </p>
        <div className="mt-3">
          <SelectField
            label="Show"
            value={caseFilter ?? "all"}
            options={FEED_CHOICES}
            optionLabel={(choice) => choice.label}
            onChange={(id) => onChoose(id as FeedChoice)}
          />
        </div>
        {caseFilter !== null ? (
          <p className="mt-2 max-w-prose text-ink-dim text-sm">{ACROSS_PROJECTS}</p>
        ) : null}
      </header>

      {caseFilter !== null ? (
        <CaseList
          rows={ordered}
          filter={caseFilter}
          context={rowContext}
          truncated={truncated}
          isPending={isPending}
          isError={isError}
          error={error}
          resolveError={resolveError}
          onRetry={onRetry}
          onShowAll={() => onChoose("all")}
        />
      ) : isPending ? (
        // Statements, not rows: the feed is sentences, and a skeleton shaped like a
        // table would promise the wrong screen.
        <div className="px-4">
          <SkeletonRows rows={3} label="Loading findings" />
        </div>
      ) : isError ? (
        <div className="px-4">
          {/* AC18. Nothing here may read as reassurance: the feed could not be read,
              and saying anything about the system's health would be a claim we cannot
              support. */}
          <InlineFailure title="The findings could not be loaded" error={error} onRetry={onRetry} />
          {/* Said in the screen's own words, because `InlineFailure` builds its
              detail from the error and cannot carry this. It is the sentence AC18
              exists for: the absence of findings here means we could not look, and
              a reader must not take it for good news. */}
          <p className="mt-3 text-ink-dim text-sm">
            This is not the same as nothing being wrong. We could not read the feed, so we cannot
            tell you either way.
          </p>
        </div>
      ) : (
        <>
          {/* Above everything in the feed, findings included: "detection is not
              running" matters most when the list looks quiet, and still matters when
              it does not. The muted list says nothing about the system, so not there. */}
          {detectionLine && !showingMuted ? (
            <section className="shrink-0 border-line border-t px-4 py-4">
              <h2
                className={`font-medium font-serif text-lg tracking-tight ${
                  detectionLine.wrong ? "text-critical-text" : "text-ink"
                }`}
              >
                {detectionLine.title}
              </h2>
            </section>
          ) : null}
          {ordered.length > 0 ? (
            <>
              <ul className="shrink-0 border-line border-t">
                {ordered.map((found) => (
                  <li key={found.id}>
                    <FindingRow finding={found} context={rowContext} />
                  </li>
                ))}
              </ul>
              {truncated ? (
                <p className="shrink-0 px-4 pt-3 text-ink-dim text-xs">
                  Showing the first {formatCount(FEED_LIMIT)} findings: open ones first, then the
                  most recent.
                </p>
              ) : null}
            </>
          ) : showingMuted ? (
            // Not the summary: an empty muted list says nothing about the system.
            <p className="shrink-0 border-line border-t px-4 py-6 text-ink-dim text-sm">
              No finding is muted.
            </p>
          ) : (
            <SummaryState totals={totals} points={points} checks={checks} detection={detection} />
          )}
          <MutedLink count={mutedCount} showing={showingMuted} onToggle={onToggleMuted} />
        </>
      )}
    </div>
  );
}

/**
 * When the feed is empty, the RED summary is the content.
 *
 * Named for what it is rather than for what it says: it used to be `HealthyState`,
 * which was the assumption behind the defect — an empty feed was taken for a
 * healthy system, and the headline said so over evidence that disagreed.
 *
 * The sentence reports only what the checks said (`headline.ts`), and it never speaks for
 * the whole system. The numbers beneath it are there to be read, because nothing else
 * reads them yet.
 */
function SummaryState({
  totals,
  points,
  checks,
  detection,
}: {
  totals?: ProjectTotals;
  points: readonly OverviewPoint[];
  checks?: CheckCounts;
  detection?: DetectionStatus;
}) {
  const headline = headlineFor(totals, checks);
  // `service_count` of zero is the honest signal: nothing reported in this
  // window, rather than something reporting nothing. The zero readings and the
  // charts go — a screen of zeroes reads as an outage — and the words say how
  // to start, where the setup pages are, and that a quiet hour is not a new
  // project (FR3, AC3).
  const reporting = totals !== undefined && totals.service_count > 0;

  return (
    <section className="shrink-0 border-line border-t px-4 py-6">
      <h2
        className={`font-medium font-serif text-lg tracking-tight ${
          headline.wrong ? "text-critical-text" : "text-ink"
        }`}
      >
        {headline.title}
      </h2>

      <p className="mt-2 text-ink-dim text-sm">
        {totals === undefined ? (
          "Send a span, a log or a metric and this fills in."
        ) : !reporting ? (
          <>
            No service has sent a span, a log or a metric in this window. Point an OpenTelemetry SDK
            at your endpoint and the first one appears within about a minute — or widen the time
            range if you expected something older.
          </>
        ) : (
          <>
            {formatCount(totals.service_count)} {plural(totals.service_count, "service")} reporting
            {checks && checks.total > 0
              ? ` · ${formatCount(checks.passing)} of ${formatCount(checks.total)} ${plural(checks.total, "synthetic check")} passing`
              : ""}
            {headline.wrong ? ". Synthetics has the failing ones." : ""}
          </>
        )}
      </p>

      {totals !== undefined && !reporting ? (
        <p className="mt-2 text-sm">
          <a
            href={SETUP_DOCS_PATH}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 font-medium text-brand-text underline-offset-2 hover:underline"
          >
            How to send your first telemetry
            <ArrowUpRight size={12} aria-hidden="true" />
          </a>
        </p>
      ) : null}

      <WhatFindingsWillSay detection={detection} />

      {/* Overview's own summary. Nothing assesses these yet, so the reader has to. */}
      {reporting && totals ? (
        <dl className="mt-5 flex flex-wrap gap-x-8 gap-y-2 border-line-subtle border-t pt-4">
          <Reading term="Requests/min" value={formatRatePerMinute(totals.requests_per_minute)} />
          <Reading
            term="Errors"
            value={formatPercent(totals.error_rate)}
            critical={totals.error_count > 0}
          />
          <Reading term="p95" value={formatDuration(totals.p95_ms)} />
        </dl>
      ) : null}

      {/* Overview's three charts, from the same `/services/overview` answer the
          summary already reads, so this costs no extra scan (FR3). The first
          days of a trial show proof that telemetry arrived, and the sentence
          still leads. */}
      {reporting && points.length > 0 ? (
        <>
          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            {buildOverviewPlots(points).map((plot) => (
              <FeedPanel key={plot.metric} plot={plot} />
            ))}
          </div>
          <p className="mt-4 text-sm">
            <Link
              to="/overview"
              className="font-medium text-brand-text underline-offset-2 hover:underline"
            >
              Open Overview for the whole system
            </Link>
          </p>
        </>
      ) : null}
    </section>
  );
}

/**
 * F3 FR46, FR55: before any finding, what a finding's page will say and from when: a
 * labelled sample from fixed data, the day the first findings can open, read from the
 * project's own data, and how deploy facts start. The date deploy causes can first reach
 * "likely" needs the project's first deploy record, which no route gives yet
 * (`status.md`, *Open decisions*).
 */
function WhatFindingsWillSay({ detection }: { detection?: DetectionStatus }) {
  const firstDay = detection?.earliest_judging_date ?? null;
  return (
    <div className="mt-4 flex max-w-prose flex-col gap-1 text-sm">
      {firstDay ? (
        <p className="text-ink">First findings from {formatCalendarDay(firstDay)}.</p>
      ) : null}
      <p className="text-ink-dim">
        Each finding says what lines up with it and where to look first;{" "}
        <a href={setupDocUrl("deploys")} className={TEXT_LINK}>
          send deploys to get deploy facts
        </a>
        . Deploy causes can reach "likely" once deploy notifications have run for 7 days.
      </p>
      <p>
        <Link to="/findings/sample" className={TEXT_LINK}>
          See a sample explanation
        </Link>
      </p>
    </div>
  );
}

const TEXT_LINK = "font-medium text-brand-text underline-offset-2 hover:underline";

/**
 * One of the three charts, as Overview draws it, without the deploy rules: the
 * deploys are their own read, and this screen must not add one for an empty feed.
 */
function FeedPanel({ plot }: { plot: OverviewPlot }) {
  return (
    <section className="rounded-sm border border-line p-3">
      <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{plot.label}</h3>
      {plot.flatAtZero ? (
        // Held at the chart's own height so the row of three stays level.
        <p className="flex items-center text-ink-dim text-sm" style={{ height: CHART_HEIGHT }}>
          {plot.zeroText}
        </p>
      ) : (
        <TrendChart
          label={plot.lines.map((line) => line.label)}
          seriesToken={plot.seriesToken}
          data={[plot.lines[0].data[0], ...plot.lines.map((line) => line.data[1])]}
          formatValue={FEED_AXIS_FORMAT[plot.metric]}
          description={`${plot.label} across this window.`}
        />
      )}
    </section>
  );
}

/** Each panel's own unit. A raw number on the axis says nothing. */
const FEED_AXIS_FORMAT: Record<OverviewPlot["metric"], (value: number) => string> = {
  rate: (value) => formatRatePerMinute(value),
  errorRate: (value) => `${value.toFixed(1)}%`,
  latency: (value) => formatDuration(value),
};

function Reading({
  term,
  value,
  critical = false,
}: {
  term: string;
  value: string;
  critical?: boolean;
}) {
  return (
    <div>
      <dt className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{term}</dt>
      <dd className={`mt-0.5 font-mono text-base ${critical ? "text-critical-text" : "text-ink"}`}>
        {value}
      </dd>
    </div>
  );
}

/**
 * The way to the muted findings, and back.
 *
 * Quiet on purpose: a muted finding is one somebody already decided to stop hearing
 * about, so it earns a link under the feed, not a place in it.
 */
function MutedLink({
  count,
  showing,
  onToggle,
}: {
  count: number;
  showing: boolean;
  onToggle: () => void;
}) {
  if (!showing && count === 0) return null;

  return (
    <div className="shrink-0 px-4 py-3">
      <button
        type="button"
        onClick={onToggle}
        className="text-brand-text text-xs underline-offset-2 hover:underline"
      >
        {showing ? "Back to the feed" : `Muted (${formatCount(count)})`}
      </button>
    </div>
  );
}

/** The API's cap on one read of the feed (`GET /findings`, at most 200). */
const FEED_LIMIT = 200;

const SEVERITY_WORD: Record<FindingSeverity, string> = {
  critical: "Critical",
  warning: "Warning",
};

const SEVERITY_TEXT: Record<FindingSeverity, string> = {
  critical: "text-critical-text",
  warning: "text-warning-text",
};

/** What every row needs beside its finding: the filter it is shown under, and who reads it. */
interface RowContext {
  caseFilter: CaseFilter | null;
  viewerId: string | null;
  zone?: string;
  /** Resolve is offered on this list's rows (FR52). */
  canResolve: boolean;
  onResolve: (findingId: string) => void;
  resolvingId: string | null;
}

/**
 * The card's sentence, which opens the finding's own page (D12). It carries the filter,
 * so the page's back link returns to the same list and names it (AC128).
 */
function FindingLink({ finding, filter }: { finding: FeedFinding; filter: CaseFilter | null }) {
  return (
    <Link
      to="/findings/$findingId"
      params={{ findingId: finding.id }}
      search={filter === null ? {} : { case: filter }}
      className="underline-offset-2 hover:underline"
    >
      {finding.title}
    </Link>
  );
}

/** A joined finding names the case it is part of (design, *The feed*). */
function PartOfACase({ finding }: { finding: FeedFinding }) {
  if (finding.root_finding_id == null) return null;
  return (
    <>
      {" · "}
      <Link
        to="/findings/$findingId"
        params={{ findingId: finding.root_finding_id }}
        className="text-brand-text underline-offset-2 hover:underline"
      >
        Part of a case
      </Link>
    </>
  );
}

/** What a card is about. A project finding has no single service: it spans several. */
function subjectOf(finding: FeedFinding): string {
  return finding.service_name ?? "This project";
}

/** A case filter's list: rows, its own empty sentence, its own failure (AC109, AC128). */
function CaseList({
  rows,
  filter,
  context,
  truncated,
  isPending,
  isError,
  error,
  resolveError,
  onRetry,
  onShowAll,
}: {
  rows: readonly FeedFinding[];
  filter: CaseFilter;
  context: RowContext;
  truncated: boolean;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  resolveError?: unknown;
  onRetry: () => void;
  onShowAll: () => void;
}) {
  if (isPending) {
    return (
      <div className="px-4">
        <SkeletonRows rows={3} label="Loading cases" />
      </div>
    );
  }
  if (isError) {
    return (
      <div className="px-4">
        <InlineFailure title="The cases could not be loaded" error={error} onRetry={onRetry} />
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="shrink-0 border-line border-t px-4 py-6">
        <p className="max-w-prose text-ink text-sm">{NO_CASES[filter]}</p>
        <Button variant="secondary" className="mt-3" onClick={onShowAll}>
          Show all
        </Button>
      </div>
    );
  }
  return (
    <>
      {resolveError ? (
        <div className="px-4 pb-3">
          <InlineFailure title="That case was not resolved" error={resolveError} />
        </div>
      ) : null}
      <ul className="shrink-0 border-line border-t">
        {rows.map((found) => (
          <li key={found.id}>
            <CaseRow finding={found} context={context} />
          </li>
        ))}
      </ul>
      {truncated ? (
        <p className="shrink-0 px-4 pt-3 text-ink-dim text-xs">Showing the newest 200.</p>
      ) : null}
    </>
  );
}

/**
 * A case row: the full statement shape (design, *The feed*), never a closed row's one
 * quiet truncated line. The title, then both questions in one sentence (FR23), the
 * case's severity in a word, the project in a case filter, the idle warning in its
 * last 14 days (FR26), and Resolve where FR52 offers it. No sparkline: a case filter
 * reads no telemetry (NFR3). No "Acked" (D47).
 */
function CaseRow({ finding, context }: { finding: FeedFinding; context: RowContext }) {
  const [confirming, setConfirming] = useState(false);
  const sentence = caseSentence({
    finding,
    root: finding,
    viewerId: context.viewerId,
    zone: context.zone,
  });
  const warning =
    finding.case_ends_by === "idle" ? caseEndingSentence(finding, context.zone) : null;
  const resolving = context.resolvingId === finding.id;
  return (
    <article className="flex gap-3 border-line-subtle border-b px-4 py-3">
      <span className="flex w-2 shrink-0 justify-center pt-1.5">
        <span
          aria-hidden="true"
          className={`size-1.5 rounded-full ${
            finding.case_severity === "critical" ? "bg-critical-solid" : "bg-warning-solid"
          }`}
        />
      </span>
      <div className="min-w-0 flex-1">
        <h3 className="text-ink text-sm">
          <FindingLink finding={finding} filter={context.caseFilter} />
        </h3>
        <p className="mt-1 max-w-prose text-ink text-sm">{sentence}</p>
        <p className="mt-0.5 font-mono text-2xs text-ink-dim">
          <span className={SEVERITY_TEXT[finding.case_severity]}>
            {SEVERITY_WORD[finding.case_severity]}
          </span>
          {" · "}
          {subjectOf(finding)}
          {context.caseFilter !== null && finding.project_name ? ` · ${finding.project_name}` : ""}
          <PartOfACase finding={finding} />
        </p>
        {warning !== null ? (
          <p className="mt-1 max-w-prose text-sm text-warning-text">{warning}</p>
        ) : null}
        {context.canResolve && finding.case_state === "investigating" ? (
          <div className="mt-2 flex flex-col gap-2">
            <Button
              variant="secondary"
              className="min-h-11 w-full max-w-xs justify-center sm:w-auto"
              pending={resolving}
              pendingLabel="Resolving"
              onClick={() => {
                // FR6: one tap with the signal closed; while it is open, say so first.
                if (finding.case_signal === "open") setConfirming(true);
                else context.onResolve(finding.id);
              }}
            >
              Resolve
            </Button>
            {confirming ? (
              <ConfirmPanel label="Resolve">
                <p className="max-w-prose">
                  The signal is still open: the problem is still being measured. Resolving says the
                  work is done anyway.
                </p>
                <div className="flex gap-2">
                  <Button
                    pending={resolving}
                    pendingLabel="Resolving"
                    onClick={() => {
                      setConfirming(false);
                      context.onResolve(finding.id);
                    }}
                  >
                    Resolve anyway
                  </Button>
                  <Button variant="secondary" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                </div>
              </ConfirmPanel>
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}

/**
 * One finding.
 *
 * What is committed is the shape: a sentence leading, the entity under it, the evidence
 * inline. An open finding with a likely cause says it in one code-written line, with the
 * same word as its page and a deploy's rate; it wraps rather than truncates, so the card
 * shows no less doubt than the page (F3 FR18, AC67). The v1 engine reports no impact
 * figure, so that has no line.
 *
 * A closed finding is one quiet line naming how it closed: no sparkline, nothing to do.
 * One whose case is still being worked keeps the full case row, in its own tier (FR21),
 * and an open finding with a case states both questions under its title (FR23).
 */
function FindingRow({ finding, context }: { finding: FeedFinding; context: RowContext }) {
  if (finding.state === "closed" && finding.case_state === "investigating") {
    return <CaseRow finding={finding} context={context} />;
  }
  if (finding.state === "closed") {
    return (
      <div className="border-line-subtle border-b px-4 py-2 text-sm">
        <p className="truncate text-ink-dim">
          <FindingLink finding={finding} filter={null} />
        </p>
        <p className="mt-0.5 text-2xs text-ink-dim">
          {finding.closed_reason ? CLOSED_AS[finding.closed_reason] : "Closed"}
          {" · "}
          <span className="font-mono">
            {finding.closed_at ? formatRelativeTime(finding.closed_at) : UNKNOWN}
          </span>
          <PartOfACase finding={finding} />
        </p>
      </div>
    );
  }

  const subject = subjectOf(finding);

  return (
    <article className="flex gap-3 border-line-subtle border-b px-4 py-3">
      <span className="flex w-2 shrink-0 justify-center pt-1.5">
        <span
          aria-hidden="true"
          className={`size-1.5 rounded-full ${
            finding.severity === "critical" ? "bg-critical-solid" : "bg-warning-solid"
          }`}
        />
      </span>

      <div className="min-w-0 flex-1">
        {/* The title is the product: a sentence, not a metric expression. */}
        <h3 className="text-ink text-sm">
          <FindingLink finding={finding} filter={null} />
        </h3>
        {finding.likely_cause_sentence ? (
          <p data-testid="card-cause" className="mt-0.5 max-w-prose text-ink text-sm">
            <Segments segments={finding.likely_cause_sentence} zone={context.zone} />
          </p>
        ) : null}
        {finding.case_state !== null ? (
          <p className="mt-0.5 max-w-prose text-ink text-sm">
            {caseSentence({
              finding,
              root: finding,
              viewerId: context.viewerId,
              zone: context.zone,
            })}
          </p>
        ) : null}
        {/* The severity in a word as well as the dot's colour (design principle 6). */}
        <p className="mt-0.5 font-mono text-2xs text-ink-dim">
          <span className={SEVERITY_TEXT[finding.severity]}>{SEVERITY_WORD[finding.severity]}</span>
          {" · "}
          {subject}
          <PartOfACase finding={finding} />
        </p>
      </div>

      <div className="flex shrink-0 items-start gap-3">
        <Sparkline
          points={finding.trend}
          max={Math.max(...finding.trend, 1)}
          tone={finding.severity === "critical" ? "critical" : "series"}
          label={`How ${subject} has been trending.`}
        />
        <span className="font-mono text-2xs text-ink-dim">
          {finding.service_muted_until !== null
            ? "Muted · "
            : finding.service_snoozed_until !== null
              ? "Snoozed · "
              : null}
          {/* D47: a case says who has it; "Acked" stays only on a finding with no case. */}
          {finding.state === "acknowledged" && finding.case_state === null
            ? "Acked"
            : formatRelativeTime(finding.started_at)}
        </span>
      </div>
    </article>
  );
}

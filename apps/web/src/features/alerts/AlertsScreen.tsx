import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan } from "@/shared/identity";
import { useSelection } from "@/shared/selection";
import type { CheckChoice } from "./alert-words";
import {
  describeCondition,
  describeDuration,
  describeHappening,
  describeTold,
  ruleStatus,
} from "./alert-words";
import type { AlertRule, EvaluationStatus, Incident } from "./api";
import { useAlertRules, useCheckChoices, useEvaluationStatus, useIncidents } from "./api";

/**
 * What is wrong now, what is watching, and what happened lately (FR22).
 *
 * In that order, because it is the order of the questions: someone who arrives from
 * an alert email wants the open incident, someone setting up wants the rules, and the
 * last thirty days are there to be looked back on.
 */

const ACTION =
  "rounded-sm px-3 py-1.5 font-medium text-sm transition-colors focus-visible:outline-2 focus-visible:outline-brand";

const SEVERITY_RULE = {
  critical: "border-l-critical-solid",
  warning: "border-l-warning-solid",
} as const;

const TONE_TEXT = {
  critical: "font-medium text-critical-text",
  warning: "font-medium text-warning-text",
  none: "text-ink-dim",
} as const;

export function AlertsScreen() {
  const canOperate = useCan("operate_alerts");
  const { projectId } = useSelection();
  const incidents = useIncidents(projectId);
  const rules = useAlertRules(projectId);
  const checks = useCheckChoices(projectId);
  const evaluation = useEvaluationStatus();

  return (
    <div className="flex h-full min-h-0 flex-col gap-8 overflow-y-auto px-4 pt-5 pb-10">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Alerts</h1>
          <p className="mt-1 text-ink-dim text-sm">
            What is breaking now, the rules that watch this project, and where they send.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            to="/settings/channels"
            className={`${ACTION} border border-line text-ink-dim hover:border-line-strong hover:text-ink`}
          >
            Channels
          </Link>
          {canOperate ? (
            <Link
              to="/alerts/rules/new"
              search={{}}
              className={`${ACTION} bg-brand text-brand-contrast hover:bg-brand-hover`}
            >
              New rule
            </Link>
          ) : null}
        </div>
      </header>

      <EvaluationBehind status={evaluation.data} />

      <section aria-labelledby="open-incidents" className="flex flex-col gap-3">
        <h2 id="open-incidents" className="font-medium font-serif text-base text-ink">
          Open now
        </h2>
        {incidents.isPending ? (
          <SkeletonRows rows={2} label="Loading open incidents" />
        ) : incidents.isError ? (
          <InlineFailure
            title="Open incidents could not be loaded"
            error={incidents.error}
            onRetry={() => void incidents.refetch()}
          />
        ) : incidents.data.open.length === 0 ? (
          <p className="text-ink-dim text-sm">{nothingOpen(incidents.data.recent)}</p>
        ) : (
          <IncidentList incidents={incidents.data.open} />
        )}
      </section>

      <section aria-labelledby="alert-rules" className="flex flex-col gap-3">
        <h2 id="alert-rules" className="font-medium font-serif text-base text-ink">
          Rules
        </h2>
        {rules.isPending ? (
          <SkeletonRows rows={3} label="Loading rules" />
        ) : rules.isError ? (
          <InlineFailure
            title="Rules could not be loaded"
            error={rules.error}
            onRetry={() => void rules.refetch()}
          />
        ) : rules.data.length === 0 ? (
          <EmptyState
            headingLevel={3}
            title="No rules"
            detail="Nothing in this project will tell anyone when it breaks. A rule names what to watch and who to tell."
            action={
              canOperate ? (
                <Link
                  to="/alerts/rules/new"
                  search={{}}
                  className={`${ACTION} bg-brand text-brand-contrast hover:bg-brand-hover`}
                >
                  New rule
                </Link>
              ) : undefined
            }
          />
        ) : (
          <RuleList rules={rules.data} checks={checks.data ?? []} canOperate={canOperate} />
        )}
      </section>

      <section aria-labelledby="recent-incidents" className="flex flex-col gap-3">
        <h2 id="recent-incidents" className="font-medium font-serif text-base text-ink">
          Last 30 days
        </h2>
        {incidents.isPending ? (
          <SkeletonRows rows={3} label="Loading recent incidents" />
        ) : incidents.isError ? null : incidents.data.recent.length === 0 ? (
          <p className="text-ink-dim text-sm">No incidents in the last 30 days.</p>
        ) : (
          <IncidentList incidents={incidents.data.recent} />
        )}
      </section>
    </div>
  );
}

function nothingOpen(recent: readonly Incident[]): string {
  const last = recent.find((incident) => incident.resolved_at !== null);
  return last?.resolved_at
    ? `Nothing is open. The last incident resolved ${formatRelativeTime(last.resolved_at)}.`
    : "Nothing is open.";
}

function IncidentList({ incidents }: { incidents: readonly Incident[] }) {
  return (
    <ul className="max-w-4xl border-line border-t">
      {incidents.map((incident) => (
        <li
          key={incident.id}
          className={`border-line-subtle border-b border-l-2 py-3 pl-3 ${SEVERITY_RULE[incident.severity]}`}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
            <div className="min-w-0">
              <p className="flex flex-wrap items-baseline gap-x-2">
                {/* The words carry the severity; the rule on the left only repeats it. */}
                <span
                  className={`font-mono text-2xs uppercase tracking-caps ${TONE_TEXT[incident.severity]}`}
                >
                  {incident.severity}
                </span>
                <IncidentSubject incident={incident} />
                <span className="text-ink-dim text-sm">{happening(incident)}</span>
                {incident.flapping ? <Chip>Flapping</Chip> : null}
                {incident.muted ? <Chip>Muted</Chip> : null}
              </p>
              <p className="mt-1 text-ink-dim text-sm">
                {incident.rule_name} · Told: {describeTold(incident)}
              </p>
              {incident.detail.reason ? (
                <p className="mt-1 text-ink-dim text-sm">Why: {incident.detail.reason}</p>
              ) : null}
            </div>
            <p className="flex shrink-0 items-baseline gap-3 text-ink-dim text-sm tabular-nums">
              <time dateTime={incident.opened_at} title={formatTimestamp(incident.opened_at)}>
                {incident.resolved_at === null
                  ? `for ${describeDuration(incident.opened_at, null)}`
                  : `lasted ${describeDuration(incident.opened_at, incident.resolved_at)}`}
              </time>
              <Link
                to="/alerts/incidents/$incidentId"
                params={{ incidentId: incident.id }}
                aria-label={`Details of ${incident.subject_label}`}
                className="text-brand-text underline-offset-2 hover:underline"
              >
                Details
              </Link>
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function happening(incident: Incident): string {
  if (incident.resolved_at !== null && !incident.is_event) return "recovered";
  return describeHappening(incident);
}

const SWEEP_NAMES: Record<EvaluationStatus["sweeps"][number]["name"], string> = {
  thresholds: "Error rate and latency rules",
  silence: "Silence rules",
  new_errors: "New-error rules",
};

/**
 * Said when the service conditions are not being judged now — so a quiet list is
 * not read as a healthy one (spec, *Error cases*). Check rules are judged the
 * moment a check changes state, so they are never behind in this way.
 */
function EvaluationBehind({ status }: { status: EvaluationStatus | undefined }) {
  const behind = status?.sweeps.filter((sweep) => sweep.behind) ?? [];
  if (behind.length === 0) return null;
  return (
    <div
      role="status"
      className="max-w-4xl border-warning-solid border-l-2 bg-warning-subtle px-3 py-2"
    >
      {behind.map((sweep) => (
        <p key={sweep.name} className="text-ink text-sm">
          {SWEEP_NAMES[sweep.name]} are not being evaluated now:{" "}
          {sweep.skipped
            ? `${sweep.skipped}.`
            : sweep.finished_at
              ? `last evaluated ${formatRelativeTime(sweep.finished_at)}.`
              : "they have not run since the scheduler started."}
        </p>
      ))}
    </div>
  );
}

function IncidentSubject({ incident }: { incident: Incident }) {
  const service = incident.detail.service;
  if (service && incident.condition_kind !== "new_error_group") {
    return (
      <Link
        to="/services/$name"
        params={{ name: service }}
        className="font-medium text-base text-ink underline-offset-2 hover:underline"
      >
        {incident.subject_label}
      </Link>
    );
  }
  const checkId = incident.detail.check_id;
  // A deleted check's page is "No such check"; its incident's Details still reads.
  if (checkId && !incident.check_deleted) {
    return (
      <Link
        to="/synthetics/$checkId"
        params={{ checkId }}
        search={{}}
        className="font-medium text-base text-ink underline-offset-2 hover:underline"
      >
        {incident.subject_label}
      </Link>
    );
  }
  return <span className="font-medium text-base text-ink">{incident.subject_label}</span>;
}

function RuleList({
  rules,
  checks,
  canOperate,
}: {
  rules: readonly AlertRule[];
  checks: readonly CheckChoice[];
  /** A rule opens in its editor; for someone who cannot edit, it is read here only. */
  canOperate: boolean;
}) {
  return (
    <ul className="max-w-4xl border-line border-t">
      {rules.map((rule) => {
        const status = ruleStatus(rule);
        return (
          <li key={rule.id} className="border-line-subtle border-b py-3">
            <RuleRowFrame ruleId={rule.id} linked={canOperate}>
              <div className="min-w-0">
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-base text-ink group-hover:underline">
                    {rule.name}
                  </span>
                  {rule.is_default ? <Chip>Default</Chip> : null}
                </p>
                <p className="mt-1 text-ink text-sm">{describeCondition(rule, checks)}</p>
                <p className="mt-1 text-ink-dim text-sm">
                  {rule.channels.length === 0
                    ? "No channels"
                    : `To ${rule.channels.map((channel) => channel.name).join(", ")}`}
                </p>
              </div>
              <span className={`shrink-0 text-sm ${TONE_TEXT[status.tone]}`}>{status.label}</span>
            </RuleRowFrame>
          </li>
        );
      })}
    </ul>
  );
}

function RuleRowFrame({
  ruleId,
  linked,
  children,
}: {
  ruleId: string;
  linked: boolean;
  children: ReactNode;
}) {
  const layout = "group flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1";
  return linked ? (
    <Link to="/alerts/rules/$ruleId" params={{ ruleId }} className={layout}>
      {children}
    </Link>
  ) : (
    <div className={layout}>{children}</div>
  );
}

function Chip({ children }: { children: string }) {
  return (
    <span className="rounded-sm border border-line px-1.5 py-0.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
      {children}
    </span>
  );
}

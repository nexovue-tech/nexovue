import { Link, useParams } from "@tanstack/react-router";

import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { setSelectedProjectId, useSelection } from "@/shared/selection";

import {
  describeHappening,
  describeIncidentState,
  describeNotification,
  describeTold,
} from "./alert-words";
import type { Incident } from "./api";
import { useIncident } from "./api";

const TONE_TEXT = {
  critical: "text-critical-text",
  warning: "text-warning-text",
} as const;

const LINK = "font-medium text-brand-text text-sm underline-offset-2 hover:underline";

/**
 * One incident: what every alert message links to (FR16). It outlives what it is
 * about — a message is read days later, and the check it named may be gone by then —
 * so it reads from the incident alone and links on to the evidence while it exists.
 */
export function IncidentScreen() {
  const { incidentId } = useParams({ from: "/shell/alerts/incidents/$incidentId" });
  const incident = useIncident(incidentId);

  return (
    <div className="flex h-full min-h-0 flex-col gap-6 overflow-y-auto px-4 pt-5 pb-10">
      <Link to="/alerts" className="text-ink-dim text-sm hover:text-ink">
        All alerts
      </Link>
      {incident.isPending ? (
        <SkeletonRows rows={4} label="Loading the incident" />
      ) : incident.isError ? (
        <InlineFailure
          title="The incident could not be loaded"
          error={incident.error}
          onRetry={() => void incident.refetch()}
        />
      ) : (
        <IncidentDetail incident={incident.data} />
      )}
    </div>
  );
}

function IncidentDetail({ incident }: { incident: Incident }) {
  const open = incident.resolved_at === null && !incident.is_event;
  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <p className="flex flex-wrap items-baseline gap-x-2 font-mono text-2xs uppercase tracking-caps">
          <span className={`font-medium ${TONE_TEXT[incident.severity]}`}>{incident.severity}</span>
          <span className="text-ink-dim">{open ? "Open" : "Over"}</span>
          {incident.flapping ? <span className="text-ink-dim">Flapping</span> : null}
          {incident.muted ? <span className="text-ink-dim">Muted</span> : null}
        </p>
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
          {incident.condition_kind === "new_error_group"
            ? `A new error: ${incident.subject_label}`
            : `${incident.subject_label} ${describeHappening(incident)}`}
        </h1>
        <p className={`text-sm ${open ? TONE_TEXT[incident.severity] : "text-ink-dim"}`}>
          {describeIncidentState(incident)}
        </p>
      </header>

      <section aria-labelledby="incident-facts" className="flex flex-col gap-1">
        <h2 id="incident-facts" className="font-medium font-serif text-base text-ink">
          What happened
        </h2>
        {incident.detail.reason ? (
          <p className="text-ink text-sm">Why: {incident.detail.reason}</p>
        ) : null}
        {incident.detail.location ? (
          <p className="text-ink-dim text-sm">Seen from: {incident.detail.location}</p>
        ) : null}
        <p className="text-ink-dim text-sm">
          {incident.is_event ? "Seen" : "Started"}: <Moment iso={incident.opened_at} />
        </p>
        {incident.resolved_at && !incident.is_event ? (
          <p className="text-ink-dim text-sm">
            Ended: <Moment iso={incident.resolved_at} />
          </p>
        ) : null}
        <p className="text-ink-dim text-sm">
          Rule:{" "}
          {incident.rule_id ? (
            <Link
              to="/alerts/rules/$ruleId"
              params={{ ruleId: incident.rule_id }}
              className="text-ink underline-offset-2 hover:underline"
            >
              {incident.rule_name}
            </Link>
          ) : (
            `${incident.rule_name} (since deleted)`
          )}
        </p>
      </section>

      <section aria-labelledby="incident-evidence" className="flex flex-col gap-1">
        <h2 id="incident-evidence" className="font-medium font-serif text-base text-ink">
          Evidence
        </h2>
        <Evidence incident={incident} />
      </section>

      <section aria-labelledby="incident-told" className="flex flex-col gap-1">
        <h2 id="incident-told" className="font-medium font-serif text-base text-ink">
          Who was told
        </h2>
        {incident.notifications.length === 0 ? (
          <p className="text-ink-dim text-sm">{describeTold(incident)}.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {incident.notifications.map((notification) => (
              <li
                key={notification.id}
                className={`text-sm ${notification.state === "failed" ? "text-critical-text" : "text-ink"}`}
              >
                {describeNotification(notification)}
                {notification.delivered_at ? (
                  <span className="text-ink-dim">
                    {" "}
                    · <Moment iso={notification.delivered_at} />
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * Where to look next. The evidence reads against the incident's own project, so a
 * link followed while another project is selected switches to it first.
 */
function Evidence({ incident }: { incident: Incident }) {
  const { projectId } = useSelection();
  const intoProject = () => {
    if (projectId !== incident.project_id) setSelectedProjectId(incident.project_id);
  };
  const { check_id: checkId, service } = incident.detail;

  if (incident.condition_kind === "check_state" && checkId) {
    return incident.check_deleted ? (
      <p className="text-ink-dim text-sm">
        The check this was about has since been deleted, and its runs with it. What it recorded is
        above.
      </p>
    ) : (
      <Link
        to="/synthetics/$checkId"
        params={{ checkId }}
        search={{}}
        onClick={intoProject}
        className={LINK}
      >
        Open the check and its runs
      </Link>
    );
  }
  if (incident.condition_kind === "new_error_group" && service) {
    return (
      <Link to="/errors" search={{ service_name: service }} onClick={intoProject} className={LINK}>
        Open the errors in {service}
      </Link>
    );
  }
  if (service) {
    return (
      <Link to="/services/$name" params={{ name: service }} onClick={intoProject} className={LINK}>
        Open {service}
      </Link>
    );
  }
  return <p className="text-ink-dim text-sm">—</p>;
}

function Moment({ iso }: { iso: string }) {
  return (
    <time dateTime={iso} title={formatTimestamp(iso)}>
      {formatTimestamp(iso)} ({formatRelativeTime(iso)})
    </time>
  );
}

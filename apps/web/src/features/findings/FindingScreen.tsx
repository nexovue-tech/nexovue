import { Link, useParams } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useState } from "react";

import { ApiError, isConflict } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import type { SeriesToken } from "@/shared/components/TrendChart";
import { TrendChart } from "@/shared/components/TrendChart";
import { useDetectionSettings, useQuietService } from "@/shared/detection/api";
import { QUIET_CHOICES, quietUntil } from "@/shared/detection/quiet";
import { formatRelativeTime } from "@/shared/format/time";
import { formatZonedTime } from "@/shared/format/zoned";
import { useCan } from "@/shared/identity";
import { setSelectedProjectId, useSelection } from "@/shared/selection";

import type { FindingDetail, FindingSignalKind } from "./api";
import { useFinding, useFindingAction } from "./api";
import type { DoneAction } from "./finding-page";
import {
  availableActions,
  describeSignal,
  formatSignalValue,
  newNormalSentence,
  notYetSentence,
  PLOT_LINES,
  SIGNAL_NAMES,
  signalPlot,
  successSentence,
} from "./finding-page";
import { CLOSED_AS } from "./finding-words";

const TONE_TEXT = {
  critical: "text-critical-text",
  warning: "text-warning-text",
} as const;

/** Failure share is drawn in the critical tone, as on a service's page; the rest in a hue. */
const SERIES_TOKEN: Record<FindingSignalKind, SeriesToken | "critical"> = {
  slower: 1,
  failing: "critical",
  traffic: 2,
};

const LINK = "font-medium text-brand-text text-sm underline-offset-2 hover:underline";
/** Sentences are capped to a readable measure; the page itself uses the window. */
const PROSE = "max-w-prose";

/**
 * One finding: what every finding message and every card links to (D12, FR31).
 *
 * The sentence leads, as on the card; the evidence follows — each signal's values and
 * its readings against the normal band, the linked incidents, and what people said.
 * It outlives its readings: after 35 days the stored values still read, and the chart
 * says it is gone (AC40). Times are on the project's clock, named.
 *
 * What a person may do follows the API's table (FR23, AC39): a viewer sees no action,
 * a member may acknowledge and call a false alarm, an admin may also set a new normal
 * and mute the service. Each confirms inline, as the rest of the console does (C11).
 */
export function FindingScreen() {
  const { findingId } = useParams({ from: "/shell/findings/$findingId" });
  const finding = useFinding(findingId);
  const settings = useDetectionSettings(finding.data?.project_id);

  return (
    <div className="flex h-full min-h-0 flex-col gap-6 overflow-y-auto px-4 pt-5 pb-10">
      <Link to="/findings" className="text-ink-dim text-sm hover:text-ink">
        All findings
      </Link>
      {finding.isPending ? (
        <SkeletonRows rows={5} label="Loading the finding" />
      ) : finding.isError ? (
        <InlineFailure
          title="The finding could not be loaded"
          error={finding.error}
          onRetry={() => void finding.refetch()}
        />
      ) : (
        <FindingPage
          finding={finding.data}
          zone={settings.data?.timezone}
          onReload={() => void finding.refetch()}
        />
      )}
    </div>
  );
}

function FindingPage({
  finding,
  zone,
  onReload,
}: {
  finding: FindingDetail;
  /** The project's IANA zone; the browser's until it is read. */
  zone?: string;
  onReload: () => void;
}) {
  return (
    <div className="flex flex-col gap-8">
      <Header finding={finding} zone={zone} />
      <Controls finding={finding} zone={zone} onReload={onReload} />
      <Signals finding={finding} />
      <Readings finding={finding} />
      {finding.scope === "project" ? <Services finding={finding} /> : null}
      <Incidents finding={finding} zone={zone} />
      <Feedback finding={finding} />
    </div>
  );
}

function Header({ finding, zone }: { finding: FindingDetail; zone?: string }) {
  return (
    <header className="flex flex-col gap-1">
      <p className="flex flex-wrap items-baseline gap-x-2 font-mono text-2xs uppercase tracking-caps">
        <span className={`font-medium ${TONE_TEXT[finding.severity]}`}>{finding.severity}</span>
        {finding.muted ? <span className="text-ink-dim">Muted</span> : null}
      </p>
      {/* The title is the product: a sentence, not a metric expression. */}
      <h1 className={`font-semibold font-serif text-ink text-xl tracking-tight ${PROSE}`}>
        {finding.title}
      </h1>
      <p className="text-ink-dim text-sm">
        <Subject finding={finding} /> · {describeState(finding, zone)}
      </p>
      {finding.muted ? (
        <p className={`text-ink-dim text-sm ${PROSE}`}>
          It opened while its service was muted, so nothing was or will be sent about it.
        </p>
      ) : null}
    </header>
  );
}

/** Where the evidence is: the service's own page, in the finding's project. */
function Subject({ finding }: { finding: FindingDetail }) {
  const { projectId } = useSelection();
  if (finding.service_name === null) return <span>This project</span>;
  return (
    <Link
      to="/services/$name"
      params={{ name: finding.service_name }}
      onClick={() => {
        if (projectId !== finding.project_id) setSelectedProjectId(finding.project_id);
      }}
      className="font-mono text-ink hover:underline"
    >
      {finding.service_name}
    </Link>
  );
}

function describeState(finding: FindingDetail, zone?: string): string {
  const since = formatZonedTime(finding.started_at, { zone });
  if (finding.state === "closed") {
    const how = finding.closed_reason ? CLOSED_AS[finding.closed_reason] : "Closed";
    return finding.closed_at ? `${how} at ${formatZonedTime(finding.closed_at, { zone })}` : how;
  }
  if (finding.state === "acknowledged") {
    const who = finding.acknowledged_by_name ?? "Someone";
    const when = finding.acknowledged_at ? ` ${formatRelativeTime(finding.acknowledged_at)}` : "";
    return `Open since ${since}. ${who} acknowledged it${when}`;
  }
  return `Open since ${since}`;
}

type Armed = "false-alarm" | "new-normal" | "mute";

/**
 * The actions this person may take, each confirmed inline. A finding that changed
 * meanwhile answers 409: the page says so and reads it again (design, *action failed*).
 */
function Controls({
  finding,
  zone,
  onReload,
}: {
  finding: FindingDetail;
  zone?: string;
  onReload: () => void;
}) {
  const canOperate = useCan("operate_alerts");
  // Also decides whether a false alarm returns the finding's minutes to the normal (D28).
  const canConfigure = useCan("configure_detection");
  const allowed = availableActions(finding, { canOperate, canConfigure });
  const act = useFindingAction(finding.id);
  const quiet = useQuietService(finding.project_id, finding.service_name ?? "");
  const [armed, setArmed] = useState<Armed>();
  const [reason, setReason] = useState("");
  const [muteChoice, setMuteChoice] = useState(QUIET_CHOICES.mute[1]?.id ?? "");
  const [done, setDone] = useState<string>();
  const [changedMeanwhile, setChangedMeanwhile] = useState<string>();

  const run = (action: DoneAction) => {
    const wasOpen = finding.state !== "closed";
    setDone(undefined);
    setChangedMeanwhile(undefined);
    act.mutate(
      { action, reason },
      {
        onSuccess: (changed) => {
          setArmed(undefined);
          setDone(successSentence(action, changed, { wasOpen }));
        },
        onError: (error) => {
          if (!isConflict(error)) return;
          setArmed(undefined);
          setChangedMeanwhile(
            `${problemDetail(error) ?? "This finding changed since you opened it."} It is shown as it is now.`,
          );
          onReload();
        },
      },
    );
  };

  const mute = () => {
    const choice = QUIET_CHOICES.mute.find((each) => each.id === muteChoice);
    if (choice === undefined) return;
    setDone(undefined);
    quiet.mutate(
      { kind: "mute", until: quietUntil(choice) },
      {
        onSuccess: (controls) => {
          setArmed(undefined);
          setDone(
            controls.muted_until
              ? `${finding.service_name} is muted until ${formatZonedTime(controls.muted_until, { zone })}. Its findings are still recorded.`
              : `${finding.service_name} is no longer muted.`,
          );
        },
      },
    );
  };

  const preview = finding.new_normal;
  const failure = act.error && !isConflict(act.error) ? act.error : quiet.error;
  // What an action did still shows once the finding has closed and nothing is left to do.
  if (!Object.values(allowed).some(Boolean) && !done && !changedMeanwhile && !failure) {
    return null;
  }

  return (
    <section aria-label="What you can do" className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {allowed.acknowledge ? (
          <Button
            variant="secondary"
            pending={act.isPending && act.variables?.action === "acknowledge"}
            pendingLabel="Acknowledging"
            onClick={() => run("acknowledge")}
          >
            Acknowledge
          </Button>
        ) : null}
        {allowed.falseAlarm ? (
          <Button variant="secondary" onClick={() => setArmed("false-alarm")}>
            False alarm
          </Button>
        ) : null}
        {allowed.newNormal ? (
          <Button variant="secondary" onClick={() => setArmed("new-normal")}>
            This is the new normal
          </Button>
        ) : null}
        {allowed.mute ? (
          <Button variant="secondary" onClick={() => setArmed("mute")}>
            Mute this service
          </Button>
        ) : null}
      </div>

      {armed === "false-alarm" ? (
        <Confirm
          onCancel={() => setArmed(undefined)}
          onConfirm={() => run("false-alarm")}
          pending={act.isPending}
          confirmLabel={
            finding.state === "closed" ? "Mark as a false alarm" : "Close as a false alarm"
          }
        >
          <p className={PROSE}>
            {finding.state === "closed"
              ? "It has already closed. Marking it counts it as a false alarm; nothing is sent."
              : "It closes now, and the channels told about it hear that it closed. The same signals cannot open a finding again until they have read normal for 10 minutes."}
            {canConfigure
              ? " Its minutes become part of the normal again."
              : " Its minutes stay out of the normal: only an admin's mark returns them."}
          </p>
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
              Why (optional)
            </span>
            <textarea
              rows={2}
              maxLength={1000}
              className={`rounded border border-line bg-transparent px-2 py-1.5 text-ink text-sm ${PROSE}`}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
        </Confirm>
      ) : null}

      {armed === "new-normal" && finding.service_name !== null ? (
        preview?.available ? (
          <Confirm
            onCancel={() => setArmed(undefined)}
            onConfirm={() => run("new-normal")}
            pending={act.isPending}
            confirmLabel="Make this the new normal"
          >
            <p className={PROSE}>
              {newNormalSentence(finding.service_name, preview)} The finding closes, and this level
              opens nothing from now on. The daily and weekly pattern is kept.
            </p>
          </Confirm>
        ) : (
          <p className={`text-ink-dim text-sm ${PROSE}`}>
            {preview ? notYetSentence(preview) : "Only an open finding can become the new normal."}
          </p>
        )
      ) : null}

      {armed === "mute" ? (
        <Confirm
          onCancel={() => setArmed(undefined)}
          onConfirm={mute}
          pending={quiet.isPending}
          confirmLabel={`Mute ${finding.service_name}`}
        >
          <p className={PROSE}>
            Nothing is sent about {finding.service_name} until the mute ends, and nothing is sent
            for what it covered after. Its findings are still recorded, under Muted.
          </p>
          <div className="max-w-xs">
            <SelectField
              label="For"
              value={muteChoice}
              options={QUIET_CHOICES.mute}
              onChange={setMuteChoice}
              optionLabel={(choice) => choice.label}
              stacked
            />
          </div>
        </Confirm>
      ) : null}

      {done ? (
        <p role="status" className={`text-ink text-sm ${PROSE}`}>
          {done}
        </p>
      ) : null}
      {changedMeanwhile ? (
        <p role="alert" className={`text-warning-text text-sm ${PROSE}`}>
          {changedMeanwhile}
        </p>
      ) : null}
      {failure ? <InlineFailure title="That did not go through" error={failure} /> : null}
    </section>
  );
}

function problemDetail(error: unknown): string | undefined {
  return error instanceof ApiError ? error.problem?.detail : undefined;
}

function Confirm({
  children,
  confirmLabel,
  pending,
  onConfirm,
  onCancel,
}: {
  children: ReactNode;
  confirmLabel: string;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded border border-warning-border bg-warning-subtle p-4 text-sm">
      {children}
      <div className="flex gap-2">
        <Button pending={pending} pendingLabel="Saving" onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** Each signal's normal, current and worst values, and what it was compared with. */
function Signals({ finding }: { finding: FindingDetail }) {
  if (finding.signals.length === 0) return null;
  return (
    <section aria-labelledby="finding-signals" className="flex flex-col gap-2">
      <h2 id="finding-signals" className="font-medium font-serif text-base text-ink">
        What moved
      </h2>
      <ul className="flex flex-col gap-2">
        {finding.signals.map((entry, index) => (
          <li key={`${entry.signal}-${entry.endpoint ?? index}`} className={`text-sm ${PROSE}`}>
            <span className="font-medium text-ink">
              {SIGNAL_NAMES[entry.signal]}
              {entry.endpoint ? <span className="font-mono"> · {entry.endpoint}</span> : null}
            </span>
            <span className="text-ink"> — {describeSignal(entry)}</span>
            {entry.comparison ? (
              <span className="block text-ink-dim">Compared with {entry.comparison}.</span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Each signal's readings against its normal band (AC38). The band is the space between
 * the normal and the minimum change the sensitivity asks for: a reading past its edge
 * can be unusual, one inside it cannot.
 */
function Readings({ finding }: { finding: FindingDetail }) {
  if (finding.scope === "project") return null;
  return (
    <section aria-labelledby="finding-readings" className="flex flex-col gap-3">
      <h2 id="finding-readings" className="font-medium font-serif text-base text-ink">
        Readings
      </h2>
      {finding.series.length === 0 ? (
        // AC40: the stored values above still stand.
        <p className={`text-ink-dim text-sm ${PROSE}`}>
          The chart is no longer available: readings are kept for at most 35 days, and none remain
          for this finding. The values above were stored with it.
        </p>
      ) : (
        finding.series.map((series) => {
          const plot = signalPlot(series);
          return (
            <figure key={series.signal} className="flex flex-col gap-1">
              <figcaption className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
                {SIGNAL_NAMES[series.signal]}
              </figcaption>
              <TrendChart
                label={PLOT_LINES}
                seriesToken={SERIES_TOKEN[series.signal]}
                data={plot.data}
                formatValue={(value) => formatSignalValue(series.unit, value)}
                description={`${SIGNAL_NAMES[series.signal]}: each judged minute against its normal and the edge of its normal band, ${series.points.length} minutes.`}
                showPoints={series.points.length < 30}
              />
            </figure>
          );
        })
      )}
    </section>
  );
}

/** FR15: a project finding has no service of its own; it names the ones it holds. */
function Services({ finding }: { finding: FindingDetail }) {
  return (
    <section aria-labelledby="finding-services" className="flex flex-col gap-2">
      <h2 id="finding-services" className="font-medium font-serif text-base text-ink">
        Services affected
      </h2>
      {finding.services.length === 0 ? (
        <p className="text-ink-dim text-sm">—</p>
      ) : (
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {finding.services.map((service) => (
            <li key={service}>
              <Link
                to="/services/$name"
                params={{ name: service }}
                className="font-mono text-ink text-sm hover:underline"
              >
                {service}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Incidents({ finding, zone }: { finding: FindingDetail; zone?: string }) {
  if (finding.incidents.length === 0) return null;
  return (
    <section aria-labelledby="finding-incidents" className="flex flex-col gap-2">
      <h2 id="finding-incidents" className="font-medium font-serif text-base text-ink">
        Linked alerts
      </h2>
      <ul className="flex flex-col gap-1">
        {finding.incidents.map((incident) => (
          <li key={incident.id} className="text-sm">
            <Link
              to="/alerts/incidents/$incidentId"
              params={{ incidentId: incident.id }}
              className={LINK}
            >
              {incident.rule_name}
            </Link>
            <span className="text-ink-dim">
              {" "}
              · opened {formatZonedTime(incident.opened_at, { zone })}
              {incident.resolved_at
                ? `, ended ${formatZonedTime(incident.resolved_at, { zone })}`
                : ", still open"}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** What a person said: a false alarm's reason, shown only to people (design 2.1, m2). */
function Feedback({ finding }: { finding: FindingDetail }) {
  if (finding.closed_reason !== "false_alarm" || !finding.feedback_reason) return null;
  return (
    <section aria-labelledby="finding-feedback" className="flex flex-col gap-1">
      <h2 id="finding-feedback" className="font-medium font-serif text-base text-ink">
        Why it was a false alarm
      </h2>
      <p className={`text-ink text-sm ${PROSE}`}>{finding.feedback_reason}</p>
    </section>
  );
}

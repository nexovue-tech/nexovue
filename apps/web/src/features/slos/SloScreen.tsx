import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Tabs } from "@/shared/components/Tabs";
import { useCan } from "@/shared/identity";

import type { SloView } from "./api";
import { useChangeSlo, useDeleteSlo, useSlo } from "./api";
import { SloBadge } from "./SloBadge";
import { SloDefinitionTab } from "./SloDefinitionTab";
import { SloHistoryTab } from "./SloHistoryTab";
import { SloMonthsTab } from "./SloMonthsTab";
import {
  deleteWarningOf,
  durationOf,
  liveState,
  NEVER_COLLAPSED,
  nextStepOf,
  QUALIFIERS_SHOWN,
  qualifierLine,
  sentenceOf,
  timeIn,
} from "./slo-words";

/** FR31: a deleted target's daily figures go this many days after it is deleted. */
const RESTORE_DAYS = 30;

/**
 * `/slos/$sloId`: where this target stands, what was not measured, who is told (F19
 * design, *One target*). Sentence and next step first, before any figure.
 */
export function SloScreen() {
  const { sloId } = useParams({ strict: false }) as { sloId: string };
  const target = useSlo(sloId);

  if (target.isPending) return <SkeletonRows rows={5} label="Loading the target" />;
  if (target.isError) {
    return (
      <div className="px-4 py-5">
        <InlineFailure
          title="This target could not be loaded"
          error={target.error}
          onRetry={() => target.refetch()}
        />
      </div>
    );
  }
  // Every time on the page shows the project's zone (FR13, AC108).
  return <Target view={target.data} zone={target.data.zone} />;
}

function Target({ view, zone }: { view: SloView; zone: string }) {
  const now = Date.now();
  const { state } = liveState(view, now);
  const next = nextStepOf(state, view);
  const canConfigure = useCan("configure_slos");
  const shown = { ...view, state, attention: state === "not_up_to_date" || view.attention };

  return (
    <div className="flex flex-col gap-5 px-4 py-5">
      <header className="flex flex-col gap-1">
        <p className="text-ink-dim text-sm">
          <Link to="/slos" className="text-brand">
            Reliability targets
          </Link>
        </p>
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">{view.name}</h1>
        <p className="text-ink-dim text-sm">{view.label}</p>
      </header>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section aria-label="Where it stands" className="flex flex-col gap-2">
          <p>
            <SloBadge view={shown} />
          </p>
          <p aria-live="polite" className="max-w-prose text-base text-ink">
            {sentenceOf(view, zone, now)}
          </p>
          {next ? <p className="max-w-prose font-medium text-ink text-sm">{next}</p> : null}
          <Qualifiers view={view} />
        </section>
        <Measured view={view} zone={zone} />
      </div>
      <TargetTabs view={view} zone={zone} />
      {canConfigure ? <Actions view={view} /> : null}
    </div>
  );
}

type Tab = "months" | "history" | "definition";
const TABS = [
  { id: "months", label: "By month" },
  { id: "history", label: "History" },
  { id: "definition", label: "Definition" },
] as const;

/** The design's tabs, after the figures: *By month*, *History*, *Definition*. *Exclusions*
 * joins with Step 4. */
function TargetTabs({ view, zone }: { view: SloView; zone: string }) {
  const [tab, setTab] = useState<Tab>("months");
  return (
    <Tabs
      label="About this target"
      items={TABS}
      selected={tab}
      onSelect={(id) => setTab(id as Tab)}
    >
      {tab === "months" ? <SloMonthsTab view={view} /> : null}
      {tab === "history" ? <SloHistoryTab sloId={view.id} zone={zone} /> : null}
      {tab === "definition" ? <SloDefinitionTab view={view} /> : null}
    </Tabs>
  );
}

function Qualifiers({ view }: { view: SloView }) {
  const [open, setOpen] = useState(false);
  const pinned = view.qualifiers.filter((each) => NEVER_COLLAPSED.includes(each));
  const rest = view.qualifiers.filter((each) => !NEVER_COLLAPSED.includes(each));
  const room = Math.max(0, QUALIFIERS_SHOWN - pinned.length);
  const shown = [...pinned, ...(open ? rest : rest.slice(0, room))];
  const hidden = rest.length - (open ? rest.length : Math.min(rest.length, room));
  if (shown.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <ul className="flex max-w-prose flex-col gap-1 text-ink-dim text-sm">
        {shown.map((qualifier) => (
          <li key={qualifier}>{qualifierLine(qualifier, view)}</li>
        ))}
      </ul>
      {hidden > 0 ? (
        <button
          type="button"
          className="self-start text-brand text-sm"
          onClick={() => setOpen(true)}
        >
          {hidden} more {hidden === 1 ? "note" : "notes"}
        </button>
      ) : null}
    </div>
  );
}

function Measured({ view, zone }: { view: SloView; zone: string }) {
  const figures = view.figures;
  const coverage = figures.coverage === null ? "—" : `${Math.floor(figures.coverage * 100)}%`;
  return (
    <section aria-label="What was measured" className="flex flex-col gap-2 text-sm">
      <h2 className="font-semibold text-ink">What was measured</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-ink-dim">Figure</dt>
        <dd className="font-mono text-ink">
          {figures.figure_percent === null ? "—" : `${figures.figure_percent}%`} against{" "}
          {Number(view.objective_percent)}%, by whole days
        </dd>
        <dt className="text-ink-dim">{view.allowance_words}</dt>
        <dd className="font-mono text-ink">
          {figures.left} of {figures.allowed}
        </dd>
        <dt className="text-ink-dim">Measured</dt>
        <dd className="text-ink">{coverage} of the window</dd>
        <dt className="text-ink-dim">May have been lost by nexovue</dt>
        <dd className="text-ink">{durationOf(figures.lost_minutes)}</dd>
        <dt className="text-ink-dim">Longest quiet stretch, 7 days</dt>
        <dd className="text-ink">{durationOf(view.longest_quiet_7d_minutes)}</dd>
      </dl>
      <p className="text-ink-dim">
        {view.evaluated_minute
          ? `As of ${timeIn(view.evaluated_minute, zone)}`
          : "Not worked out yet"}
      </p>
    </section>
  );
}

function Actions({ view }: { view: SloView }) {
  const navigate = useNavigate();
  const change = useChangeSlo(view.id);
  const remove = useDeleteSlo();
  const [confirming, setConfirming] = useState(false);
  const purgedOn = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" }).format(
    Date.now() + RESTORE_DAYS * 86_400_000,
  );
  return (
    <section aria-label="Change this target" className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3">
        <Link
          to="/slos/$sloId/edit"
          params={{ sloId: view.id }}
          className="font-medium text-brand text-sm"
        >
          Edit
        </Link>
        <Button
          variant="secondary"
          pending={change.isPending}
          onClick={() => change.mutate({ enabled: !view.enabled })}
        >
          {view.enabled ? "Disable" : "Turn alerts back on"}
        </Button>
        <Button variant="secondary" onClick={() => setConfirming(true)}>
          Delete
        </Button>
      </div>
      {view.enabled ? (
        <p className="max-w-prose text-ink-dim text-sm">
          Disabling stops alerts and closes any open burn. It keeps counting.
        </p>
      ) : null}
      {confirming ? (
        <ConfirmPanel label="Delete this target">
          <p className="max-w-prose">{deleteWarningOf(view.figures.days_held, purgedOn)}</p>
          <div className="flex gap-3">
            <Button
              pending={remove.isPending}
              onClick={() => remove.mutate(view.id, { onSuccess: () => navigate({ to: "/slos" }) })}
            >
              Delete
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </div>
        </ConfirmPanel>
      ) : null}
      {change.isError ? (
        <InlineFailure title="The target could not be changed" error={change.error} />
      ) : null}
      {remove.isError ? (
        <InlineFailure title="The target could not be deleted" error={remove.error} />
      ) : null}
    </section>
  );
}

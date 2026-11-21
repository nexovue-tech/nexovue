import { type FormEvent, useEffect, useRef, useState } from "react";

import type { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { useCan } from "@/shared/identity";

import type { Dashboard, DashboardDocument } from "./api";
import { useCreateDashboard, useDashboards, useDeleteDashboard } from "./api";
import { PurgeConfirm } from "./PurgeConfirm";
import { downloadDashboard, EXPORT_WARNING } from "./transfer";
import { Versions } from "./Versions";

/**
 * What can be done to a whole dashboard (F20, T3.6). Delete goes through the shared inline
 * `ConfirmPanel`, saying what goes in real numbers and that it can be restored for 30 days
 * (FR25, AC136), with export offered first, since a file is a copy nobody can delete.
 *
 * Duplicate asks for a new title and shows the titles in use, because copying is the sprawl
 * this feature exists to avoid (FR26, AC78). Versions lists the history and restores one as
 * a draft (FR51); an owner or admin may remove the dashboard, or one version, at once
 * (FR68). Its own component so the read view can mount it as well (plan TF.1).
 */

const RESTORABLE_DAYS = 30;
const COPY_SUFFIX = " (copy)";

type Open = "duplicate" | "versions" | "purge" | undefined;

export function DashboardActions({
  dashboard,
  onDeleted,
  onDuplicated,
  onRestoreVersion,
  onPurged,
  now = Date.now(),
}: {
  dashboard: Dashboard;
  onDeleted: () => void;
  /** A copy was made: the page goes to it. */
  onDuplicated?: (copy: Dashboard) => void;
  /** A kept version, as the builder's draft. */
  onRestoreVersion?: (document: DashboardDocument) => void;
  /** The whole dashboard was removed at once: there is nothing left to stay on. */
  onPurged?: () => void;
  now?: number;
}) {
  const canEdit = useCan("edit_dashboards");
  const canPurge = useCan("purge_dashboards");
  const [open, setOpen] = useState<Open>();
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteDashboard(dashboard.id);
  const deleteOpener = useRef<HTMLSpanElement>(null);
  const panels = dashboard.definition.panels.length;
  const opened = dashboard.last_viewed_at
    ? `last opened ${formatRelativeTime(dashboard.last_viewed_at, now)}`
    : "never opened";

  const givingBack = useRef(false);
  // Back to the button that asked, once it exists again: where a keyboard user expects to be.
  useEffect(() => {
    if (confirming || !givingBack.current) return;
    givingBack.current = false;
    deleteOpener.current?.querySelector("button")?.focus();
  }, [confirming]);

  const keep = () => {
    givingBack.current = true;
    setConfirming(false);
  };
  const sentence = `, ${panels} ${panels === 1 ? "panel" : "panels"}, ${opened}. It can be restored for ${RESTORABLE_DAYS} days.`;

  const toggle = (which: Exclude<Open, undefined>) => setOpen(open === which ? undefined : which);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {canEdit ? (
          <Button variant="secondary" onClick={() => toggle("duplicate")}>
            Duplicate this dashboard
          </Button>
        ) : null}
        <Button variant="secondary" onClick={() => toggle("versions")}>
          Versions
        </Button>
        {canEdit && !confirming ? (
          <span ref={deleteOpener}>
            <Button variant="secondary" onClick={() => setConfirming(true)}>
              Delete this dashboard
            </Button>
          </span>
        ) : null}
        {canPurge ? (
          <Button variant="secondary" onClick={() => toggle("purge")}>
            Remove at once
          </Button>
        ) : null}
      </div>
      {open === "duplicate" ? (
        <Duplicate
          dashboard={dashboard}
          onCancel={() => setOpen(undefined)}
          onDuplicated={(copy) => onDuplicated?.(copy)}
        />
      ) : null}
      {open === "versions" ? (
        <Versions
          dashboardId={dashboard.id}
          title={dashboard.title}
          onRestore={onRestoreVersion}
          now={now}
        />
      ) : null}
      {open === "purge" ? (
        <PurgeConfirm
          dashboardId={dashboard.id}
          title={dashboard.title}
          onDone={() => onPurged?.()}
          onCancel={() => setOpen(undefined)}
        />
      ) : null}
      {confirming ? (
        <ConfirmPanel label={`Delete ${visibleEscapes(dashboard.title)}`}>
          <p>
            <em>{visibleEscapes(dashboard.title)}</em>
            {sentence}
          </p>
          <p className="text-ink-dim">Export it first to keep a copy. {EXPORT_WARNING}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              pending={remove.isPending}
              pendingLabel="Deleting"
              onClick={() => remove.mutate(undefined, { onSuccess: onDeleted })}
            >
              Delete
            </Button>
            <Button variant="secondary" onClick={() => downloadDashboard(dashboard)}>
              Export it first
            </Button>
            <Button variant="secondary" onClick={keep}>
              Keep it
            </Button>
          </div>
          {remove.isError ? (
            <p className="text-critical-text">Could not delete this dashboard. Try again.</p>
          ) : null}
        </ConfirmPanel>
      ) : null}
    </div>
  );
}

/**
 * Duplicate (FR26, AC78): a copy of the saved dashboard under a title that is not in use.
 * The titles in use are shown, so the choice is made with them in view, and a title that
 * matches one is refused here in words before anything is sent; the API's unique index is
 * still the guarantee, and its words are shown if it refuses.
 */
function Duplicate({
  dashboard,
  onCancel,
  onDuplicated,
}: {
  dashboard: Dashboard;
  onCancel: () => void;
  onDuplicated: (copy: Dashboard) => void;
}) {
  const existing = useDashboards();
  const create = useCreateDashboard();
  const [title, setTitle] = useState(`${dashboard.title}${COPY_SUFFIX}`);
  const [taken, setTaken] = useState<string | undefined>();
  const typed = title.trim();
  const titles = existing.data?.map((row) => row.title) ?? [];

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (typed === "") return;
    const holder = titles.find((each) => each.toLowerCase() === typed.toLowerCase());
    if (holder !== undefined) {
      setTaken(`A dashboard named ${visibleEscapes(holder)} exists. Choose another title.`);
      return;
    }
    setTaken(undefined);
    create.mutate(
      { title: typed, description: dashboard.description, definition: dashboard.definition },
      { onSuccess: onDuplicated },
    );
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <p className="text-ink text-sm">
        Copies the saved dashboard. Give the copy a title no other dashboard has.
      </p>
      <TextField
        label="Title of the copy"
        name="copy-title"
        value={title}
        onChange={(value) => {
          setTitle(value);
          setTaken(undefined);
        }}
        error={taken ?? (create.isError ? apiWords(create.error) : undefined)}
      />
      <div>
        <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          Titles in use{existing.data ? ` (${titles.length})` : ""}
        </p>
        {existing.isError ? (
          <p className="text-ink-dim text-sm">
            Could not list them. A title already in use is refused when you save.
          </p>
        ) : (
          <ul className="mt-1 max-h-32 overflow-y-auto text-ink-dim text-sm">
            {titles.map((each) => (
              <li key={each}>{visibleEscapes(each)}</li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" pending={create.isPending} pendingLabel="Copying">
          Make a copy
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function apiWords(error: unknown): string {
  return (error as ApiError).problem?.detail ?? "Could not make the copy. Try again.";
}

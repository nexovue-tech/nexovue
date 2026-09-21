import { useId, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { UNKNOWN } from "@/shared/format/number";

import type { CheckChoice, MaintenanceWindow, Recurrence } from "./check-settings-api";
import {
  useCheckChoices,
  useDeleteMaintenanceWindow,
  useMaintenanceWindows,
  useSaveMaintenanceWindow,
} from "./check-settings-api";
import type { DraftField, MaintenanceDraft } from "./maintenance-draft";
import {
  DRAFT_FIELD_LABELS,
  describeRecurrence,
  describeScope,
  draftOf,
  draftProblems,
  emptyDraft,
  requestOf,
  sameWindow,
  serverProblems,
} from "./maintenance-draft";

/**
 * A project's maintenance windows (FR36): when failing checks are expected, and so
 * change no state.
 *
 * One form is open at a time — new, or one row's edit. Opening a second would either
 * throw away the first draft without a word or leave two half-edited windows on the
 * screen with one Save each, and the in-page switch is not something the router's
 * unsaved-changes guard can see.
 */

type Editing = { kind: "new" } | { kind: "existing"; windowId: string };

/* Weekday included: a weekly window is read by the day it falls on. */
const WHEN = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const UNTIL = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function formatWhen(startsAt: string, endsAt: string): string {
  const start = new Date(startsAt);
  const end = new Date(endsAt);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return UNKNOWN;
  return WHEN.formatRange(start, end);
}

function formatUntil(repeatUntil: string | null): string | undefined {
  if (repeatUntil === null) return undefined;
  const at = new Date(repeatUntil);
  return Number.isNaN(at.getTime()) ? UNKNOWN : UNTIL.format(at);
}

function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export function MaintenanceWindowsSection({ projectId }: { projectId: string }) {
  const windows = useMaintenanceWindows(projectId);
  const checks = useCheckChoices(projectId);
  const [editing, setEditing] = useState<Editing>();
  const [outcome, setOutcome] = useState<string>();

  const startNew = () => {
    setOutcome(undefined);
    setEditing({ kind: "new" });
  };

  const newForm =
    editing?.kind === "new" ? (
      <WindowForm
        projectId={projectId}
        checks={checks}
        onCancel={() => setEditing(undefined)}
        onSaved={(saved) => {
          setEditing(undefined);
          setOutcome(`Scheduled ${saved.name}: ${formatWhen(saved.starts_at, saved.ends_at)}.`);
        }}
      />
    ) : null;

  return (
    <section className="flex flex-col gap-4">
      <div className="max-w-2xl">
        <h3 className="font-medium font-serif text-ink text-base tracking-tight">
          Maintenance windows
        </h3>
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">
          Planned work that is expected to make checks fail. While a window is in effect, a failing
          check changes no state. Its runs are still recorded, marked as taken during maintenance,
          and left out of uptime.
        </p>
      </div>

      {outcome ? (
        <p role="status" className="max-w-2xl text-ink text-sm">
          {outcome}
        </p>
      ) : null}

      {windows.isPending ? (
        <SkeletonRows rows={2} label="Loading maintenance windows" />
      ) : windows.isError ? (
        <InlineFailure
          title="The maintenance windows could not be loaded"
          error={windows.error}
          onRetry={() => void windows.refetch()}
        />
      ) : windows.data.length === 0 && editing === undefined ? (
        <EmptyState
          headingLevel={4}
          title="No maintenance windows"
          detail="Schedule one before planned work — a deploy, a migration, a provider's own maintenance — so the checks it breaks do not report an outage."
          action={<Button onClick={startNew}>Schedule a window</Button>}
        />
      ) : (
        <>
          {editing === undefined ? (
            <div>
              <Button variant="secondary" onClick={startNew}>
                Schedule a window
              </Button>
            </div>
          ) : null}
          {newForm}
          {windows.data.length > 0 ? (
            <ul className="max-w-3xl border-line border-t">
              {windows.data.map((maintenanceWindow) =>
                editing?.kind === "existing" && editing.windowId === maintenanceWindow.id ? (
                  <li key={maintenanceWindow.id} className="border-line-subtle border-b py-3">
                    <WindowForm
                      projectId={projectId}
                      stored={maintenanceWindow}
                      checks={checks}
                      onCancel={() => setEditing(undefined)}
                      onSaved={(saved) => {
                        setEditing(undefined);
                        setOutcome(`Saved ${saved.name}.`);
                      }}
                    />
                  </li>
                ) : (
                  <WindowRow
                    key={maintenanceWindow.id}
                    projectId={projectId}
                    maintenanceWindow={maintenanceWindow}
                    checks={checks.data}
                    editable={editing === undefined}
                    onEdit={() => {
                      setOutcome(undefined);
                      setEditing({ kind: "existing", windowId: maintenanceWindow.id });
                    }}
                    onDeleted={() => setOutcome(`Deleted ${maintenanceWindow.name}.`)}
                  />
                ),
              )}
            </ul>
          ) : null}
        </>
      )}
    </section>
  );
}

function WindowRow({
  projectId,
  maintenanceWindow,
  checks,
  editable,
  onEdit,
  onDeleted,
}: {
  projectId: string;
  maintenanceWindow: MaintenanceWindow;
  checks: readonly CheckChoice[] | undefined;
  editable: boolean;
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const remove = useDeleteMaintenanceWindow(projectId);
  const [confirming, setConfirming] = useState(false);

  return (
    <li className="border-line-subtle border-b py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            {/* The words carry the state; the tint only repeats them. */}
            {maintenanceWindow.active_now ? (
              <span className="shrink-0 rounded-sm border border-info-border bg-info-subtle px-1.5 py-0.5 font-mono font-semibold text-2xs text-info-text uppercase tracking-caps">
                In effect now
              </span>
            ) : null}
            <span className="font-medium text-base text-ink">{maintenanceWindow.name}</span>
          </div>
          <p className="mt-1 text-ink text-sm">
            {formatWhen(maintenanceWindow.starts_at, maintenanceWindow.ends_at)}
          </p>
          <p className="mt-1 text-ink-dim text-sm">
            {describeRecurrence(
              maintenanceWindow.recurrence,
              formatUntil(maintenanceWindow.repeat_until),
            )}
          </p>
          <p className="mt-1 text-ink-dim text-sm">{describeScope(maintenanceWindow, checks)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="secondary" disabled={!editable} onClick={onEdit}>
            Edit
          </Button>
          <Button
            variant="secondary"
            pending={remove.isPending}
            pendingLabel="Deleting…"
            onClick={() => setConfirming(true)}
          >
            Delete
          </Button>
        </div>
      </div>

      {confirming ? (
        <div className="mt-3 border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Delete {maintenanceWindow.name}?</p>
          <p className="mt-1 text-ink-dim text-sm leading-relaxed">
            {maintenanceWindow.active_now
              ? "It is in effect now, so it ends immediately: a check that is failing changes state on its next confirmed run."
              : "Its checks will be judged normally at the times it covers."}{" "}
            Runs already recorded during it keep their maintenance mark. It cannot be undone.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Button
              onClick={() => {
                setConfirming(false);
                remove.mutate(maintenanceWindow.id, { onSuccess: onDeleted });
              }}
            >
              Yes, delete {maintenanceWindow.name}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {remove.error ? (
        <div className="mt-3">
          <InlineFailure title={`${maintenanceWindow.name} was not deleted`} error={remove.error} />
        </div>
      ) : null}
    </li>
  );
}

const RECURRENCE_OPTIONS: readonly { id: Recurrence; label: string }[] = [
  { id: "none", label: "Does not repeat" },
  { id: "daily", label: "Every day" },
  { id: "weekly", label: "Every week" },
];

function WindowForm({
  projectId,
  stored,
  checks,
  onCancel,
  onSaved,
}: {
  projectId: string;
  /** Absent for a new window. */
  stored?: MaintenanceWindow;
  checks: ReturnType<typeof useCheckChoices>;
  onCancel: () => void;
  onSaved: (saved: MaintenanceWindow) => void;
}) {
  const save = useSaveMaintenanceWindow(projectId);
  const [draft, setDraft] = useState<MaintenanceDraft>(() =>
    stored === undefined ? emptyDraft() : draftOf(stored),
  );
  // Problems appear once a save was tried, not while the first field is still being typed.
  const [attempted, setAttempted] = useState(false);

  const guard = useUnsavedChanges(!sameWindow(draft, stored));

  const local = attempted ? draftProblems(draft) : {};
  const server = serverProblems(save.error);
  const problems = { ...server, ...local };
  const problemFields = (Object.keys(problems) as DraftField[]).filter(
    (field) => problems[field] !== undefined,
  );
  // A refusal the API tied to no field — a limit, a permission — is shown whole.
  const unplacedFailure = save.error && Object.keys(server).length === 0 ? save.error : undefined;

  const update = (next: MaintenanceDraft) => {
    setDraft(next);
    if (save.error) save.reset();
  };

  const submit = () => {
    setAttempted(true);
    if (Object.keys(draftProblems(draft)).length > 0) return;
    const request = requestOf(draft, projectId);
    if (request === undefined) return;
    save.mutate({ windowId: stored?.id, request }, { onSuccess: onSaved });
  };

  const repeating = draft.recurrence !== "none";

  return (
    <form
      className="flex max-w-xl flex-col gap-4 border border-line bg-surface px-4 py-3"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <UnsavedChangesPrompt
        guard={guard}
        what={stored === undefined ? "a new maintenance window" : `the window ${stored.name}`}
      />
      <p className="font-medium text-ink text-sm">
        {stored === undefined ? "Schedule a maintenance window" : `Edit ${stored.name}`}
      </p>

      {problemFields.length > 1 ? (
        <div role="alert" className="border-critical-solid border-l-2 px-3 py-2 text-sm">
          <p className="font-medium text-ink">
            This window cannot be saved yet — {problemFields.length} fields need attention:
          </p>
          <ul className="mt-1 list-disc pl-5 text-ink-dim">
            {problemFields.map((field) => (
              <li key={field}>
                {DRAFT_FIELD_LABELS[field]}: {problems[field]}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <TextField
        label="Name"
        name="window-name"
        autoComplete="off"
        value={draft.name}
        onChange={(name) => update({ ...draft, name })}
        error={problems.name}
        hint="What the work is, so the list reads as a plan."
      />

      <p className="text-ink-dim text-xs">
        Times are in this browser&rsquo;s time zone, {browserTimeZone()}.
      </p>
      <div className="flex flex-col gap-4 sm:flex-row">
        <DateTimeField
          label="Starts"
          name="starts_at"
          value={draft.startsAt}
          onChange={(startsAt) => update({ ...draft, startsAt })}
          error={problems.starts_at}
        />
        <DateTimeField
          label="Ends"
          name="ends_at"
          value={draft.endsAt}
          onChange={(endsAt) => update({ ...draft, endsAt })}
          error={problems.ends_at}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <SelectField
          stacked
          label="Repeats"
          value={draft.recurrence}
          options={RECURRENCE_OPTIONS}
          optionLabel={(option) => option.label}
          onChange={(id) => {
            const option = RECURRENCE_OPTIONS.find((candidate) => candidate.id === id);
            if (option) update({ ...draft, recurrence: option.id });
          }}
        />
        {repeating ? (
          <p className="text-ink-dim text-xs leading-relaxed">
            Each occurrence starts exactly {draft.recurrence === "daily" ? "24 hours" : "7 days"}{" "}
            after the last, so across a daylight-saving change it moves by an hour on your clock.
          </p>
        ) : null}
      </div>

      {repeating ? (
        <DateTimeField
          label="Repeat until (optional)"
          name="repeat_until"
          value={draft.repeatUntil}
          onChange={(repeatUntil) => update({ ...draft, repeatUntil })}
          error={problems.repeat_until}
          hint="The last moment an occurrence may begin. Leave empty to repeat with no end."
        />
      ) : null}

      <ScopeField draft={draft} checks={checks} error={problems.check_ids} onChange={update} />

      <div className="flex items-center gap-2">
        <Button type="submit" pending={save.isPending} pendingLabel="Saving…">
          {stored === undefined ? "Schedule window" : "Save window"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>

      {unplacedFailure ? (
        <InlineFailure title="The maintenance window was not saved" error={unplacedFailure} />
      ) : null}
    </form>
  );
}

function ScopeField({
  draft,
  checks,
  error,
  onChange,
}: {
  draft: MaintenanceDraft;
  checks: ReturnType<typeof useCheckChoices>;
  error: string | undefined;
  onChange: (next: MaintenanceDraft) => void;
}) {
  const errorId = useId();

  const toggle = (checkId: string) =>
    onChange({
      ...draft,
      checkIds: draft.checkIds.includes(checkId)
        ? draft.checkIds.filter((id) => id !== checkId)
        : [...draft.checkIds, checkId],
    });

  return (
    <fieldset className="flex flex-col gap-2" aria-describedby={error ? errorId : undefined}>
      <legend className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Covers</legend>
      <label className="flex items-start gap-2 text-ink text-sm">
        <input
          type="radio"
          name="window-scope"
          className="mt-1"
          checked={draft.appliesToAll}
          onChange={() => onChange({ ...draft, appliesToAll: true })}
        />
        <span>
          Every check in this project
          <span className="block text-ink-dim">Including checks added after it is saved.</span>
        </span>
      </label>
      <label className="flex items-start gap-2 text-ink text-sm">
        <input
          type="radio"
          name="window-scope"
          className="mt-1"
          checked={!draft.appliesToAll}
          onChange={() => onChange({ ...draft, appliesToAll: false })}
        />
        <span>Only the checks I choose</span>
      </label>

      {draft.appliesToAll ? null : (
        <div className="ml-6 flex flex-col gap-1.5">
          {checks.isPending ? (
            <SkeletonRows rows={2} label="Loading this project's checks" />
          ) : checks.isError ? (
            <InlineFailure
              title="This project's checks could not be loaded"
              error={checks.error}
              onRetry={() => void checks.refetch()}
            />
          ) : checks.data.length === 0 ? (
            <p className="text-ink-dim text-sm">
              This project has no checks yet, so there is nothing to choose. Cover every check
              instead, and the checks you create later are included.
            </p>
          ) : (
            checks.data.map((check) => (
              <label key={check.id} className="flex items-center gap-2 text-ink text-sm">
                <input
                  type="checkbox"
                  checked={draft.checkIds.includes(check.id)}
                  onChange={() => toggle(check.id)}
                />
                <span>{check.name}</span>
                <span className="font-mono text-2xs text-ink-dim">{check.type}</span>
              </label>
            ))
          )}
        </div>
      )}

      {error ? (
        <p id={errorId} className="text-critical-text text-xs">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

/** A native `datetime-local` in TextField's register; TextField itself takes no such type. */
function DateTimeField({
  label,
  name,
  value,
  onChange,
  error,
  hint,
}: {
  label: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
}) {
  const id = useId();
  const noteId = `${id}-note`;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {label}
      </label>
      <input
        id={id}
        name={name}
        type="datetime-local"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || hint ? noteId : undefined}
        className={`rounded-sm border bg-surface px-2.5 py-1.5 font-mono text-ink text-sm outline-none focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1 ${
          error ? "border-critical-solid" : "border-line hover:border-line-strong"
        }`}
      />
      {error ? (
        <p id={noteId} className="text-critical-text text-xs">
          {error}
        </p>
      ) : hint ? (
        <p id={noteId} className="text-ink-dim text-xs">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

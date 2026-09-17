import { useId, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { plural } from "@/shared/format/plural";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";

import type { CheckVariableSummary } from "./check-settings-api";
import {
  byName,
  isTaken,
  MAX_VARIABLES_PER_PROJECT,
  secretReference,
  useCheckVariables,
  useDeleteCheckVariable,
  useWriteCheckVariable,
  variableNameProblem,
  variableReference,
  variableValueProblem,
} from "./check-settings-api";

/**
 * A project's check variables: plain values, edited in place, saved a row at a time.
 *
 * **Per row, not one save for the table.** The API writes one name per request, so a
 * table-wide save would be several requests that can half succeed — and a form that
 * reports "some of your changes were saved" leaves the reader to work out which.
 *
 * Edits are held by name and compared against what is stored, so the section is dirty
 * exactly while some row's text differs from the saved value.
 */
export function CheckVariablesSection({ projectId }: { projectId: string }) {
  const variables = useCheckVariables(projectId);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");

  const stored = variables.data ?? [];
  const editedRows = stored.filter(
    (variable) => edits[variable.name] !== undefined && edits[variable.name] !== variable.value,
  );
  const dirty = editedRows.length > 0 || newName !== "" || newValue !== "";
  const guard = useUnsavedChanges(dirty);

  const forget = (name: string) =>
    setEdits((current) => {
      const { [name]: _dropped, ...rest } = current;
      return rest;
    });

  return (
    <section className="flex flex-col gap-4">
      <UnsavedChangesPrompt guard={guard} what="this project's check variables" />
      <div className="max-w-2xl">
        <h3 className="font-medium font-serif text-ink text-base tracking-tight">
          Check variables
        </h3>
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">
          Values your checks share, such as a base URL or an account id. Write{" "}
          <code className="font-mono text-ink text-xs">{variableReference("name")}</code> in a
          check&rsquo;s URL, a header or the body. Values are visible to anyone who can read this
          project, so a token or password belongs in check secrets as{" "}
          <code className="font-mono text-ink text-xs">{secretReference("NAME")}</code>.
        </p>
      </div>

      {variables.isPending ? (
        <SkeletonRows rows={3} label="Loading check variables" />
      ) : variables.isError ? (
        <InlineFailure
          title="The check variables could not be loaded"
          error={variables.error}
          onRetry={() => void variables.refetch()}
        />
      ) : (
        <>
          {variables.data.length === 0 ? (
            <EmptyState
              headingLevel={4}
              title="This project has no check variables"
              detail="Add one below when several checks share a value, so changing it is one edit rather than one per check."
            />
          ) : (
            <ul className="max-w-3xl border-line border-t">
              {byName(variables.data).map((variable) => (
                <VariableRow
                  key={variable.name}
                  projectId={projectId}
                  variable={variable}
                  draft={edits[variable.name] ?? variable.value}
                  onDraft={(value) =>
                    setEdits((current) => ({ ...current, [variable.name]: value }))
                  }
                  onSettled={() => forget(variable.name)}
                />
              ))}
            </ul>
          )}
          {editedRows.length > 1 ? (
            <p className="text-ink-dim text-sm">
              {editedRows.length} variables have unsaved values. Each row saves on its own.
            </p>
          ) : null}
          <AddVariableForm
            projectId={projectId}
            existing={variables.data}
            name={newName}
            value={newValue}
            onName={setNewName}
            onValue={setNewValue}
            onAdded={() => {
              setNewName("");
              setNewValue("");
            }}
          />
        </>
      )}
    </section>
  );
}

type RowMode = "editing" | "confirming-delete";

function VariableRow({
  projectId,
  variable,
  draft,
  onDraft,
  onSettled,
}: {
  projectId: string;
  variable: CheckVariableSummary;
  draft: string;
  onDraft: (value: string) => void;
  /** The draft no longer differs from what is stored: saved, reverted or deleted. */
  onSettled: () => void;
}) {
  const write = useWriteCheckVariable(projectId);
  const remove = useDeleteCheckVariable(projectId);
  const [mode, setMode] = useState<RowMode>("editing");
  const [saved, setSaved] = useState(false);
  const inputId = useId();
  const errorId = `${inputId}-error`;

  const reference = variableReference(variable.name);
  const changed = draft !== variable.value;
  const problem = variableValueProblem(draft);

  const save = () => {
    if (!changed || problem) return;
    write.mutate(
      { name: variable.name, value: draft },
      {
        onSuccess: () => {
          onSettled();
          setSaved(true);
        },
      },
    );
  };

  return (
    <li className="border-line-subtle border-b py-3">
      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-start sm:gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <div className="min-w-0 sm:w-56 sm:shrink-0">
          <label htmlFor={inputId} className="block font-medium font-mono text-ink text-sm">
            {variable.name}
          </label>
          <p className="mt-1 text-ink-dim text-xs">
            <code className="font-mono">{reference}</code> ·{" "}
            <span title={formatTimestamp(variable.updated_at)}>
              updated {formatRelativeTime(variable.updated_at)}
            </span>
          </p>
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <input
            id={inputId}
            name={`variable-value-${variable.name}`}
            value={draft}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => {
              setSaved(false);
              onDraft(event.target.value);
            }}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? errorId : undefined}
            className={`w-full rounded-sm border bg-surface px-2.5 py-1.5 font-mono text-ink text-sm outline-none focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1 ${
              problem ? "border-critical-solid" : "border-line hover:border-line-strong"
            }`}
          />
          {problem ? (
            <p id={errorId} className="text-critical-text text-xs">
              {problem}
            </p>
          ) : changed ? (
            <p className="text-ink-dim text-xs">Unsaved. Revert puts back the stored value.</p>
          ) : saved ? (
            <p role="status" className="text-ink-dim text-xs">
              Saved as the value of {reference}.
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {changed ? (
            <>
              <Button
                type="submit"
                pending={write.isPending}
                pendingLabel="Saving…"
                disabled={problem !== undefined}
              >
                Save
              </Button>
              <Button variant="secondary" onClick={onSettled}>
                Revert
              </Button>
            </>
          ) : null}
          <Button
            variant="secondary"
            pending={remove.isPending}
            pendingLabel="Deleting…"
            onClick={() => setMode("confirming-delete")}
          >
            Delete
          </Button>
        </div>
      </form>

      {mode === "confirming-delete" ? (
        <div className="mt-3 border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Delete {variable.name}?</p>
          <p className="mt-1 text-ink-dim text-sm leading-relaxed">
            {variable.used_by_checks === 0 ? (
              <>
                No check uses <code className="font-mono text-ink">{reference}</code>.
              </>
            ) : (
              <>
                {variable.used_by_checks} {plural(variable.used_by_checks, "check")}{" "}
                {plural(variable.used_by_checks, "uses", "use")}{" "}
                <code className="font-mono text-ink">{reference}</code> and will become
                misconfigured, sending nothing until a variable named {variable.name} exists again.
              </>
            )}
            {changed ? " The unsaved value in this row is discarded too." : ""}
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Button
              onClick={() => {
                setMode("editing");
                remove.mutate(variable.name, { onSuccess: onSettled });
              }}
            >
              Yes, delete {variable.name}
            </Button>
            <Button variant="secondary" onClick={() => setMode("editing")}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {write.error ? (
        <div className="mt-3">
          <InlineFailure title={`${variable.name} was not saved`} error={write.error} />
        </div>
      ) : null}
      {remove.error ? (
        <div className="mt-3">
          <InlineFailure title={`${variable.name} was not deleted`} error={remove.error} />
        </div>
      ) : null}
    </li>
  );
}

function AddVariableForm({
  projectId,
  existing,
  name,
  value,
  onName,
  onValue,
  onAdded,
}: {
  projectId: string;
  existing: readonly CheckVariableSummary[];
  name: string;
  value: string;
  onName: (name: string) => void;
  onValue: (value: string) => void;
  onAdded: () => void;
}) {
  const write = useWriteCheckVariable(projectId);
  const [added, setAdded] = useState<string>();

  // `PUT` on a taken name overwrites that variable, so adding must never reach it.
  const nameProblem = isTaken(name, existing)
    ? `${name} already exists. Edit its value in the row above.`
    : variableNameProblem(name);
  const valueProblem = variableValueProblem(value);
  const atLimit = existing.length >= MAX_VARIABLES_PER_PROJECT;
  const ready = name !== "" && nameProblem === undefined && valueProblem === undefined;

  return (
    <form
      className="flex max-w-md flex-col gap-3 border border-line bg-surface px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready || atLimit) return;
        write.mutate(
          { name, value },
          {
            onSuccess: (variable) => {
              setAdded(variable.name);
              onAdded();
            },
          },
        );
      }}
    >
      <p className="font-medium text-ink text-sm">Add a variable</p>
      <TextField
        label="Variable name"
        name="variable-name"
        autoComplete="off"
        value={name}
        onChange={(next) => {
          setAdded(undefined);
          onName(next);
        }}
        error={nameProblem}
        hint={
          name === "" ? "Checks refer to it by this name." : `Used as ${variableReference(name)}`
        }
      />
      <TextField
        label="Variable value"
        name="variable-value"
        autoComplete="off"
        required={false}
        value={value}
        onChange={(next) => {
          setAdded(undefined);
          onValue(next);
        }}
        error={valueProblem}
        hint="Visible to anyone who can read this project."
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          pending={write.isPending}
          pendingLabel="Adding…"
          disabled={!ready || atLimit}
        >
          Add variable
        </Button>
        {atLimit ? (
          <span className="text-ink-dim text-xs">
            A project holds at most {MAX_VARIABLES_PER_PROJECT} variables. Delete one to add
            another.
          </span>
        ) : name === "" ? (
          <span className="text-ink-dim text-xs">A name is required.</span>
        ) : null}
      </div>
      {added ? (
        <p role="status" className="text-ink text-sm">
          Added {added}. Checks can now use{" "}
          <code className="font-mono">{variableReference(added)}</code>.
        </p>
      ) : null}
      {write.error ? (
        <InlineFailure title="The variable was not added" error={write.error} />
      ) : null}
    </form>
  );
}

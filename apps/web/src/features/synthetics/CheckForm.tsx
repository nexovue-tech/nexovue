import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";

import type { CheckDefinition, CheckInput, CreatedCheck } from "./api";
import { useProbeLocations, useRunNow, useSecretNames, useVariableNames } from "./api";
import { fieldProblemsOf, problemMessageOf } from "./api-problems";
import { CheckSettings } from "./CheckSettings";
import type { CheckDraft, FieldProblems } from "./check-draft";
import { inputFrom, problemsFromApi, sameCheck, suggestedName, validate } from "./check-draft";
import { EvidenceView } from "./EvidenceView";
import { DnsFields, TcpFields } from "./OtherKindFields";
import { RequestEditor } from "./RequestEditor";
import { StepList } from "./StepList";

type Saved = CreatedCheck | CheckDefinition;

/**
 * Create and edit share one form, so a check reads the same the day it is written and
 * the day it is changed.
 *
 * Problems are shown after the first attempt to save or run, not while someone is still
 * typing the URL they were about to finish. The API's answer lands under the same
 * fields as the form's own, because both speak the API's paths, and is dropped at the
 * next edit, which may have fixed it.
 */
export function CheckForm({
  projectId,
  initial,
  version,
  submitLabel,
  pendingLabel,
  save,
  onSaved,
}: {
  projectId: string | undefined;
  initial: CheckDraft;
  /** The stored check's `updated_at`. A new one is a change to adopt; absent while creating. */
  version?: string;
  submitLabel: string;
  pendingLabel: string;
  save: {
    mutate: (
      input: CheckInput,
      options: { onSuccess: (saved: Saved) => void; onSettled: () => void },
    ) => void;
    isPending: boolean;
    error: unknown;
    reset: () => void;
  };
  onSaved: (saved: Saved) => void;
}) {
  const [stored, setStored] = useState({ version, draft: initial });
  const [draft, setDraft] = useState(initial);
  const [attempted, setAttempted] = useState(false);
  const [changedElsewhere, setChangedElsewhere] = useState(false);
  const [selectedStep, setSelectedStep] = useState(0);
  const saving = useRef(false);
  const secrets = useSecretNames(projectId);
  const variables = useVariableNames(projectId);
  const secretNames = secrets.data ?? [];
  const variableNames = variables.data ?? [];
  const locations = useProbeLocations(projectId).data ?? [];
  /**
   * What the project defines, so a `{{name}}` nobody stored is named on the field
   * that wrote it rather than by the API against the whole request.
   *
   * **Undefined until both lists have actually arrived.** An empty list from a read
   * that is still in flight, or one that failed, looks exactly like a project with no
   * secrets — and refusing to save a valid check with "this project has no secret
   * named TOKEN" would be the form lying about a network blip. Unknown means the API
   * stays the only authority, which is what it is anyway.
   */
  const known = useMemo(
    () =>
      secrets.isSuccess && variables.isSuccess
        ? { secrets: secretNames, variables: variableNames }
        : undefined,
    [secrets.isSuccess, variables.isSuccess, secretNames, variableNames],
  );
  const runNow = useRunNow(projectId);

  // A newer stored check replaces an untouched draft and never an edited one. A save's
  // own answer is recorded by `submit`, so it is not mistaken for someone else's change.
  useEffect(() => {
    if (saving.current || version === undefined || version === stored.version) return;
    setStored({ version, draft: initial });
    if (sameCheck(stored.draft, initial)) return;
    if (sameCheck(draft, stored.draft)) {
      setDraft(initial);
      setChangedElsewhere(false);
    } else if (!sameCheck(draft, initial)) {
      setChangedElsewhere(true);
    }
  }, [version, initial, stored, draft]);

  const clientProblems = useMemo(() => validate(draft, known), [draft, known]);
  const apiProblems = useMemo(
    () =>
      problemsFromApi({ ...fieldProblemsOf(runNow.error), ...fieldProblemsOf(save.error) }, draft),
    [runNow.error, save.error, draft],
  );
  const problems: FieldProblems = attempted ? { ...apiProblems, ...clientProblems } : {};
  const dirty = !sameCheck(draft, stored.draft);
  const guard = useUnsavedChanges(dirty);
  const name = suggestedName(draft);

  const change = (next: CheckDraft) => {
    setDraft(next);
    if (save.error) save.reset();
    if (runNow.error) runNow.reset();
  };

  const submit = () => {
    setAttempted(true);
    if (Object.keys(validate(draft, known)).length > 0) return;
    const input = inputFrom({ ...draft, name: draft.name.trim() || name || "Heartbeat" });
    saving.current = true;
    save.mutate(input, {
      onSuccess: (saved) => {
        setStored({ version: saved.updated_at, draft });
        setChangedElsewhere(false);
        onSaved(saved);
      },
      onSettled: () => {
        saving.current = false;
      },
    });
  };

  const run = () => {
    setAttempted(true);
    if (Object.keys(validate(draft, known)).length > 0) return;
    const input = inputFrom(draft);
    runNow.mutate({
      name: input.name || name || "Run now",
      config: input.config,
      degraded_ms: input.degraded_ms,
      failing_ms: input.failing_ms,
    });
  };
  const runPending = runNow.isPending;
  const canRun = draft.kind !== "heartbeat";

  return (
    <form
      noValidate
      className="flex min-w-0 flex-col gap-8"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <UnsavedChangesPrompt guard={guard} what="this check" />
      {changedElsewhere ? (
        <p role="status" className="border-warning-solid border-l-2 px-3 py-2 text-ink text-sm">
          This check was changed elsewhere while you were editing. Your changes are kept; saving
          them replaces that change.
        </p>
      ) : null}

      <section className="flex min-w-0 flex-col gap-3" aria-label="What the check does">
        {draft.kind === "api" ? (
          <RequestEditor
            request={draft.request}
            onChange={(request) => change({ ...draft, request })}
            problems={problems}
            prefix="config.request"
            projectId={projectId}
            secretNames={secretNames}
            allowExtractions={false}
            onRunNow={run}
            runPending={runPending}
          />
        ) : null}

        {draft.kind === "multistep" ? (
          <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
            <div className="flex flex-col gap-3">
              <StepList
                steps={draft.steps}
                selected={Math.min(selectedStep, draft.steps.length - 1)}
                onSelect={setSelectedStep}
                onChange={(steps) => change({ ...draft, steps })}
                problems={problems}
              />
              <div>
                <Button
                  variant="secondary"
                  onClick={run}
                  pending={runPending}
                  pendingLabel="Running"
                >
                  Run all requests now
                </Button>
              </div>
            </div>
            {draft.steps.map((step, index) =>
              index === Math.min(selectedStep, draft.steps.length - 1) ? (
                <RequestEditor
                  key={step.key}
                  request={step}
                  onChange={(next) =>
                    change({
                      ...draft,
                      steps: draft.steps.map((candidate) =>
                        candidate.key === step.key ? next : candidate,
                      ),
                    })
                  }
                  problems={problems}
                  prefix={`config.steps.${index}`}
                  projectId={projectId}
                  secretNames={secretNames}
                  allowExtractions
                />
              ) : null,
            )}
          </div>
        ) : null}

        {draft.kind === "tcp" ? (
          <TcpFields draft={draft} onChange={change} problems={problems} />
        ) : null}
        {draft.kind === "dns" ? (
          <DnsFields draft={draft} onChange={change} problems={problems} />
        ) : null}
        {draft.kind === "heartbeat" ? (
          <p className="max-w-2xl text-ink-dim text-sm">
            A heartbeat is not probed. Your job calls its URL each time it succeeds; if no call
            arrives within the expected period plus the grace, the check goes down. The URL is shown
            once, when the check is created.
          </p>
        ) : null}

        {(draft.kind === "tcp" || draft.kind === "dns") && canRun ? (
          <div>
            <Button variant="secondary" onClick={run} pending={runPending} pendingLabel="Running">
              Run now
            </Button>
          </div>
        ) : null}
      </section>

      {runNow.data || runNow.error ? (
        <section
          aria-live="polite"
          aria-label="Run now result"
          className="min-w-0 border-line border-t pt-4"
        >
          <h3 className="mb-2 font-mono text-2xs text-ink-dim uppercase tracking-caps">
            Run now — nothing was recorded
          </h3>
          {runNow.data ? (
            <EvidenceView key={runNow.data.run_id} evidence={runNow.data} />
          ) : (
            <p className="text-critical-text text-sm">
              {problemMessageOf(runNow.error) ?? "The run could not be started."}
            </p>
          )}
        </section>
      ) : null}

      <section
        className="flex flex-col gap-3 border-line border-t pt-6"
        aria-label="Schedule and thresholds"
      >
        <CheckSettings
          draft={draft}
          onChange={change}
          problems={problems}
          availableLocations={locations}
          suggestedName={name}
        />
      </section>

      <ProblemSummary
        problems={problems}
        message={attempted ? problemMessageOf(save.error) : undefined}
      />

      <div className="flex items-center gap-3">
        <Button
          type="submit"
          pending={save.isPending}
          pendingLabel={pendingLabel}
          // A refused save may be sent again unchanged: a default heartbeat has nothing to change.
          disabled={!dirty && attempted && !save.error}
        >
          {submitLabel}
        </Button>
        {!dirty ? <span className="text-ink-dim text-xs">No unsaved changes.</span> : null}
      </div>
    </form>
  );
}

/** Every problem in one place, as well as beside its field (principle 3). */
function ProblemSummary({
  problems,
  message,
}: {
  problems: FieldProblems;
  message: string | undefined;
}) {
  const entries = Object.entries(problems);
  if (entries.length === 0 && !message) return null;
  return (
    <div role="alert" className="border-critical-solid border-l-2 px-3 py-2">
      <p className="font-medium text-ink text-sm">
        {entries.length > 0
          ? `This check cannot run or be saved yet: ${entries.length} ${entries.length === 1 ? "problem" : "problems"}.`
          : message}
      </p>
      {entries.length > 0 ? (
        <ul className="mt-1 list-disc pl-5 text-ink-dim text-sm">
          {entries.map(([field, reason]) => (
            <li key={field}>
              <span className="text-ink">{describeField(field)}</span>: {reason}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

const FIELD_WORDS: Record<string, string> = {
  name: "Name",
  interval_seconds: "Interval",
  locations: "Locations",
  degraded_ms: "Degraded threshold",
  failing_ms: "Failing threshold",
  retries: "Retries",
  url: "URL",
  method: "Method",
  headers: "Headers",
  query: "Query parameters",
  body: "Body",
  auth: "Authorization",
  assertions: "Assertions",
  extractions: "Extracted values",
  timeout_seconds: "Time limit",
  host: "Host",
  port: "Port",
  resolver: "Resolver",
  grace_seconds: "Grace",
};

/** `config.steps.1.assertions.0.target` → "Request 2 · Assertions 1 · target". */
export function describeField(path: string): string {
  const parts = path.split(".").filter((segment) => segment !== "config" && segment !== "request");
  const words: string[] = [];
  for (let index = 0; index < parts.length; index += 1) {
    const segment = parts[index] ?? "";
    const next = parts[index + 1];
    if (segment === "steps" && next !== undefined && /^\d+$/.test(next)) {
      words.push(`Request ${Number(next) + 1}`);
      index += 1;
    } else if (next !== undefined && /^\d+$/.test(next)) {
      words.push(`${FIELD_WORDS[segment] ?? segment} ${Number(next) + 1}`);
      index += 1;
    } else {
      words.push(FIELD_WORDS[segment] ?? segment.replaceAll("_", " "));
    }
  }
  return words.join(" · ") || "The check";
}

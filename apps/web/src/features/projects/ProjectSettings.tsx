import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { useCan } from "@/shared/identity";

import type { MaskingTextKey, ProjectDeleted, ProjectDetail, SamplingDocument } from "./api";
import {
  MASKING_RULE_NAMES,
  MASKING_TEXT_KEYS,
  useDeleteProject,
  useRenameProject,
  useSaveMaskingConfig,
  useSaveSamplingConfig,
} from "./api";
import { CheckSecretsSection } from "./CheckSecretsSection";
import { CheckVariablesSection } from "./CheckVariablesSection";
import { DetectionSection } from "./DetectionSection";
import { MaintenanceWindowsSection } from "./MaintenanceWindowsSection";
import type { MaskingForm } from "./masking";
import {
  documentOf,
  formOf,
  newlyDisabled,
  RULE_LABELS,
  sameMasking,
  TEXT_KEY_LABELS,
} from "./masking";

/**
 * One project's settings: what it is called, what it keeps, and the two documents
 * that decide what reaches the store at all.
 *
 * The masking form renders **every** key the API accepts, including the ones a
 * customer rarely touches. That is not thoroughness, it is required: the endpoint
 * replaces the whole document, so a form that drew four keys and saved would
 * delete the other three and report success.
 *
 * **Every form below seeds its state from `project` and never re-syncs.** The
 * caller mounts this under `key={project.id}`, which is React's own answer to
 * resetting state when a prop changes — an effect per form would be four places
 * for a switched project to leave the previous one's values in the fields, and
 * renaming the wrong project is what that costs.
 */
export function ProjectSettings({
  project,
  onDeleted,
}: {
  project: ProjectDetail;
  onDeleted: (result: ProjectDeleted) => void;
}) {
  // Each section is an editor, gated by the action it performs (F26, FR1). Retention
  // is a reading anyone may see; the rest appear only for who may change them.
  const canConfigure = useCan("configure_projects");
  const canManageSecrets = useCan("manage_check_secrets");
  const canOperateAlerts = useCan("operate_alerts");
  const canConfigureDetection = useCan("configure_detection");
  return (
    <div className="flex flex-col gap-10 border-line border-t pt-8">
      {canConfigure ? <Identity project={project} /> : null}
      <Retention project={project} />
      {canConfigure ? <Sampling project={project} /> : null}
      {canConfigure ? <Masking project={project} /> : null}
      {canManageSecrets ? <CheckSecretsSection projectId={project.id} /> : null}
      {canManageSecrets ? <CheckVariablesSection projectId={project.id} /> : null}
      {canOperateAlerts ? <MaintenanceWindowsSection projectId={project.id} /> : null}
      {canConfigureDetection ? <DetectionSection projectId={project.id} /> : null}
      {canConfigure ? <Deletion project={project} onDeleted={onDeleted} /> : null}
      {canConfigure ? null : (
        <p className="max-w-2xl text-ink-dim text-sm">
          Renaming, sampling, masking, detection and deleting a project are an owner's or an admin's
          to change.
        </p>
      )}
    </div>
  );
}

function SectionHeading({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="max-w-2xl">
      <h3 className="font-medium font-serif text-ink text-base tracking-tight">{title}</h3>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">{detail}</p>
    </div>
  );
}

function Identity({ project }: { project: ProjectDetail }) {
  const rename = useRenameProject();
  const [name, setName] = useState(project.name);
  const [slug, setSlug] = useState(project.slug);

  const changed = name !== project.name || slug !== project.slug;
  const guard = useUnsavedChanges(changed);

  return (
    <section className="flex flex-col gap-4">
      <UnsavedChangesPrompt guard={guard} what="this project's name or slug" />
      <SectionHeading
        title="Name and slug"
        detail="The name is what you read on a screen; the slug is what appears in URLs. Changing the slug changes those links."
      />
      <form
        className="flex max-w-md flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          rename.mutate({ projectId: project.id, name, slug });
        }}
      >
        <TextField label="Name" name="name" value={name} onChange={setName} />
        <TextField
          label="Slug"
          name="slug"
          value={slug}
          onChange={setSlug}
          hint="Lowercase letters, digits and hyphens."
        />
        <div>
          <Button
            type="submit"
            pending={rename.isPending}
            pendingLabel="Saving"
            disabled={!changed}
          >
            Save
          </Button>
        </div>
        {rename.error ? (
          <InlineFailure title="The project could not be renamed" error={rename.error} />
        ) : null}
      </form>
    </section>
  );
}

function Retention({ project }: { project: ProjectDetail }) {
  return (
    <section className="flex flex-col gap-2">
      <SectionHeading
        title="Retention"
        detail="How long raw telemetry stays readable in this project."
      />
      <p className="text-ink text-sm">
        <span className="font-mono">{project.retention_days}</span> days
      </p>
      {/* Read-only on purpose: what a project actually gets is resolved against
          the plan's ceiling by the API, so a control here would be two answers
          to one question. */}
      <p className="text-ink-dim text-sm">
        Set by your plan. It is resolved against the plan&rsquo;s ceiling, so it cannot be raised
        from this screen.
      </p>
    </section>
  );
}

/** The three fields `projects/config.py` accepts, and the bounds it enforces. */
const HEALTHY_PERCENT_MIN = 0;
const HEALTHY_PERCENT_MAX = 100;
const SLOW_THRESHOLD_MS_MIN = 0;

function Sampling({ project }: { project: ProjectDetail }) {
  const save = useSaveSamplingConfig(project.id);
  const stored = project.sampling_config;

  const [keepErrors, setKeepErrors] = useState(stored.keep_errors ?? true);
  const [slowMs, setSlowMs] = useState(String(stored.slow_threshold_ms ?? ""));
  const [healthy, setHealthy] = useState(String(stored.healthy_percent ?? ""));

  // The API refuses anything outside these rather than clamping, because a
  // project that believes it stores everything while it stores a tenth is what
  // clamping would produce. Refusing here first means saying so in the field.
  const slowNumber = slowMs === "" ? undefined : Number(slowMs);
  const healthyNumber = healthy === "" ? undefined : Number(healthy);
  const slowError =
    slowNumber !== undefined &&
    (!Number.isInteger(slowNumber) || slowNumber < SLOW_THRESHOLD_MS_MIN)
      ? `A whole number of milliseconds, ${SLOW_THRESHOLD_MS_MIN} or more.`
      : undefined;
  const healthyError =
    healthyNumber !== undefined &&
    (!Number.isInteger(healthyNumber) ||
      healthyNumber < HEALTHY_PERCENT_MIN ||
      healthyNumber > HEALTHY_PERCENT_MAX)
      ? `A whole percentage between ${HEALTHY_PERCENT_MIN} and ${HEALTHY_PERCENT_MAX}.`
      : undefined;

  const submit = () => {
    const config: SamplingDocument = { keep_errors: keepErrors };
    if (slowNumber !== undefined) config.slow_threshold_ms = slowNumber;
    if (healthyNumber !== undefined) config.healthy_percent = healthyNumber;
    save.mutate(config);
  };

  // Compared against what is stored, not against whether a field was touched: a
  // value typed and undone is not an unsaved change, and warning about it teaches
  // people to click the warning away.
  const changed =
    keepErrors !== (stored.keep_errors ?? true) ||
    slowMs !== String(stored.slow_threshold_ms ?? "") ||
    healthy !== String(stored.healthy_percent ?? "");
  const guard = useUnsavedChanges(changed);

  return (
    <section className="flex flex-col gap-4">
      <UnsavedChangesPrompt guard={guard} what="the sampling rules" />
      <SectionHeading
        title="Sampling"
        detail="Which traces are kept. Errors and slow traces are kept whole; a percentage of the healthy ones is kept so a normal request still has an example to compare against."
      />
      <form
        className="flex max-w-md flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label className="flex items-start gap-2 text-ink text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={keepErrors}
            onChange={(event) => setKeepErrors(event.target.checked)}
          />
          <span>
            Keep every trace that errored
            <span className="block text-ink-dim">
              Switching this off means an error you are investigating may not have been stored.
            </span>
          </span>
        </label>
        <TextField
          label="Slow threshold (ms)"
          name="slow_threshold_ms"
          value={slowMs}
          onChange={setSlowMs}
          required={false}
          error={slowError}
          hint="A trace at or above this is kept whole. Leave blank to use the default."
        />
        <TextField
          label="Healthy sample (%)"
          name="healthy_percent"
          value={healthy}
          onChange={setHealthy}
          required={false}
          error={healthyError}
          hint="How many of the remaining fast, successful traces to keep."
        />
        <div>
          <Button
            type="submit"
            pending={save.isPending}
            pendingLabel="Saving"
            disabled={slowError !== undefined || healthyError !== undefined}
          >
            Save sampling
          </Button>
        </div>
        {save.error ? (
          <InlineFailure title="The sampling settings were not saved" error={save.error} />
        ) : null}
      </form>
    </section>
  );
}

function Masking({ project }: { project: ProjectDetail }) {
  const save = useSaveMaskingConfig(project.id);
  const stored = project.masking_config;
  const [form, setForm] = useState<MaskingForm>(() => formOf(stored));
  const [confirming, setConfirming] = useState<string[]>();

  const toggle = (set: Set<string>, rule: string) => {
    const next = new Set(set);
    if (next.has(rule)) next.delete(rule);
    else next.add(rule);
    return next;
  };

  const submit = () => {
    const next = documentOf(form);
    const turningOff = newlyDisabled(stored, next);
    // Asked before the API refuses, and only for rules not already off — a
    // prompt that appeared on every save is a prompt people click through.
    if (turningOff.length > 0 && confirming === undefined) {
      setConfirming(turningOff);
      return;
    }
    save.mutate({ config: next, confirmDisabling: confirming ?? [] });
    setConfirming(undefined);
  };

  // Through the stored document, so the comparison is between two values of the
  // same shape rather than between a form and a document that order their sets
  // differently.
  const changed = !sameMasking(documentOf(form), stored);
  const guard = useUnsavedChanges(changed);

  return (
    <section className="flex flex-col gap-4">
      <UnsavedChangesPrompt guard={guard} what="the masking rules" />
      <SectionHeading
        title="Masking"
        detail="What is removed from your telemetry before it is stored. These rules run in your own process, so anything they catch never leaves it."
      />

      <form
        className="flex flex-col gap-6"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <fieldset className="flex flex-col gap-3">
          <legend className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
            Built-in rules
          </legend>
          {MASKING_RULE_NAMES.map((rule) => (
            <div key={rule} className="flex flex-wrap items-center gap-4 text-sm">
              <span className="w-56 text-ink">{RULE_LABELS[rule] ?? rule}</span>
              <label className="flex items-center gap-1.5 text-ink-dim">
                <input
                  type="checkbox"
                  checked={form.hashed.has(rule)}
                  onChange={() => setForm({ ...form, hashed: toggle(form.hashed, rule) })}
                />
                Hash instead of blank
              </label>
              <label className="flex items-center gap-1.5 text-ink-dim">
                <input
                  type="checkbox"
                  checked={form.disabled.has(rule)}
                  onChange={() => setForm({ ...form, disabled: toggle(form.disabled, rule) })}
                />
                Switch off
              </label>
            </div>
          ))}
        </fieldset>

        {MASKING_TEXT_KEYS.map((key: MaskingTextKey) => (
          <label key={key} className="flex max-w-md flex-col gap-1.5">
            <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
              {TEXT_KEY_LABELS[key].label}
            </span>
            <textarea
              rows={3}
              className="rounded border border-line bg-transparent px-2 py-1.5 font-mono text-ink text-sm"
              value={form.text[key]}
              onChange={(event) =>
                setForm({ ...form, text: { ...form.text, [key]: event.target.value } })
              }
            />
            <span className="text-ink-dim text-xs">{TEXT_KEY_LABELS[key].hint} One per line.</span>
          </label>
        ))}

        {confirming ? (
          <div className="max-w-2xl rounded border border-warning-border bg-warning-subtle p-4 text-sm">
            <p className="text-warning-text">
              Switching{" "}
              <strong>{confirming.map((rule) => RULE_LABELS[rule] ?? rule).join(", ")}</strong> off
              means those values will start leaving your process and being stored. Everything sent
              from that moment keeps them.
            </p>
            <div className="mt-3 flex gap-2">
              <Button type="submit" pending={save.isPending} pendingLabel="Saving">
                Yes, switch it off
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(undefined)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Button type="submit" pending={save.isPending} pendingLabel="Saving">
              Save masking
            </Button>
          </div>
        )}
        {save.error ? (
          <InlineFailure title="The masking settings were not saved" error={save.error} />
        ) : null}
      </form>
    </section>
  );
}

function Deletion({
  project,
  onDeleted,
}: {
  project: ProjectDetail;
  onDeleted: (result: ProjectDeleted) => void;
}) {
  const remove = useDeleteProject();
  const [typed, setTyped] = useState("");
  const [arming, setArming] = useState(false);

  return (
    <section className="flex flex-col gap-4">
      <SectionHeading
        title="Delete this project"
        detail="Every key that sends to it stops working immediately, and its telemetry stops being readable. This cannot be undone."
      />
      {arming ? (
        <form
          className="flex max-w-md flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            remove.mutate(project.id, { onSuccess: onDeleted });
          }}
        >
          <TextField
            label={`Type ${project.slug} to confirm`}
            name="confirm"
            value={typed}
            onChange={setTyped}
            hint="Naming the project is what stops this being a mis-click."
          />
          <div className="flex gap-2">
            <Button
              type="submit"
              pending={remove.isPending}
              pendingLabel="Deleting"
              disabled={typed !== project.slug}
            >
              Delete {project.slug}
            </Button>
            <Button variant="secondary" onClick={() => setArming(false)}>
              Cancel
            </Button>
          </div>
          {remove.error ? (
            <InlineFailure title="The project was not deleted" error={remove.error} />
          ) : null}
        </form>
      ) : (
        <div>
          <Button variant="secondary" onClick={() => setArming(true)}>
            Delete this project
          </Button>
        </div>
      )}
    </section>
  );
}

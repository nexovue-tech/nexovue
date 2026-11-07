import { useNavigate, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { Permitted } from "@/shared/components/Permitted";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { useProjectSelection } from "@/shared/projects";

import type { Sampling, SloDraft, SloView } from "./api";
import { useChangeSlo, useCreateSlo, useSlo } from "./api";
import {
  AVAILABILITY_DEFINITION,
  SAMPLING_NOT_SURE,
  SAMPLING_QUESTION,
  SAMPLING_YES,
} from "./slo-words";

const WHO_MAY = "an owner, an admin or a member";
const FIELDS = ["name", "service_name", "objective_percent", "upstream_sampling"] as const;
type Field = (typeof FIELDS)[number];
type Errors = Partial<Record<Field | "form", string>>;

const EMPTY: SloDraft = {
  name: "",
  description: "",
  service_name: "",
  objective_percent: "99.9",
  window_days: 28,
  upstream_sampling: "no",
};

/** `/slos/new`: one form on one screen (F19 design, *Creating a first target*). */
export function NewSloScreen() {
  return (
    <Permitted action="configure_slos" who={WHO_MAY}>
      <NewSlo />
    </Permitted>
  );
}

/** `/slos/$sloId/edit`: the create form with its definition lines. */
export function EditSloScreen() {
  return (
    <Permitted action="configure_slos" who={WHO_MAY}>
      <EditSlo />
    </Permitted>
  );
}

function NewSlo() {
  const { projectId } = useProjectSelection();
  const create = useCreateSlo(projectId ?? "");
  return (
    <SloForm
      title="New reliability target"
      initial={EMPTY}
      pending={create.isPending}
      save={(draft) => create.mutateAsync(draft)}
    />
  );
}

function EditSlo() {
  const { sloId } = useParams({ strict: false }) as { sloId: string };
  const target = useSlo(sloId);
  const change = useChangeSlo(sloId);
  if (target.isPending) return <SkeletonRows rows={4} label="Loading the target" />;
  if (target.isError) return <p className="px-4 py-5 text-sm">This target could not be loaded.</p>;
  return (
    <SloForm
      title={`Edit ${target.data.name}`}
      initial={draftOf(target.data)}
      pending={change.isPending}
      save={(draft) => change.mutateAsync(draft)}
    />
  );
}

function draftOf(view: SloView): SloDraft {
  return {
    name: view.name,
    description: view.description,
    service_name: view.service_name ?? "",
    objective_percent: String(Number(view.objective_percent)),
    window_days: view.window_days === 7 ? 7 : 28,
    upstream_sampling: view.upstream_sampling ?? "no",
  };
}

/** What the browser can say before asking (FR2); the API says the rest beside its field. */
function checked(draft: SloDraft): Errors {
  const errors: Errors = {};
  if (!draft.name.trim()) errors.name = "Give the target a name.";
  if (!draft.service_name.trim()) errors.service_name = "Name the service to count.";
  const objective = Number(draft.objective_percent);
  if (!Number.isFinite(objective) || objective < 90 || objective > 99.99) {
    errors.objective_percent = "Choose a target between 90% and 99.99%.";
  } else if (!/^\d+(\.\d{1,2})?$/.test(draft.objective_percent.trim())) {
    errors.objective_percent =
      "Use at most two decimals. At 99.999% over 28 days a whole outage of about 24 seconds spends it, and nexovue counts by the minute.";
  }
  return errors;
}

function refusalOf(error: unknown): Errors {
  if (!(error instanceof ApiError)) return { form: "The target could not be saved. Try again." };
  const field = error.problem?.field;
  const detail = error.problem?.detail ?? error.message;
  if (field === "name" || field === "objective_percent" || field === "service_name") {
    return { [field]: detail };
  }
  return { form: detail };
}

function SloForm({
  title,
  initial,
  pending,
  save,
}: {
  title: string;
  initial: SloDraft;
  pending: boolean;
  save: (draft: SloDraft) => Promise<SloView>;
}) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState(initial);
  const [errors, setErrors] = useState<Errors>({});
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const guard = useUnsavedChanges(dirty && !pending);
  const set = (field: keyof SloDraft) => (value: string) =>
    setDraft((current) => ({ ...current, [field]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = checked(draft);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    try {
      const saved = await save({ ...draft, objective_percent: draft.objective_percent.trim() });
      setDraft(saved ? draftOf(saved) : draft);
      await navigate({ to: "/slos/$sloId", params: { sloId: saved.id } });
    } catch (error) {
      setErrors(refusalOf(error));
    }
  }

  const summary = Object.values(errors).filter(Boolean);
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-5 px-4 py-5">
      <UnsavedChangesPrompt guard={guard} what="this target" />
      <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">{title}</h1>
      {summary.length > 0 ? (
        <div
          role="alert"
          className="max-w-prose rounded border border-critical-border bg-critical-subtle p-3 text-sm text-critical-text"
        >
          <p className="font-medium">The target was not saved:</p>
          <ul className="list-disc pl-5">
            {summary.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-4">
          <TextField
            label="Service"
            name="service_name"
            value={draft.service_name}
            onChange={set("service_name")}
            error={errors.service_name}
            required
          />
          <TextField
            label="Target, in %"
            name="objective_percent"
            value={draft.objective_percent}
            onChange={set("objective_percent")}
            error={errors.objective_percent}
            hint="Between 90% and 99.99%, to two decimals."
            required
          />
          <fieldset className="flex flex-col gap-1 text-sm">
            <legend className="font-medium text-ink">Window</legend>
            {([28, 7] as const).map((days) => (
              <label key={days} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="window_days"
                  checked={draft.window_days === days}
                  onChange={() => setDraft((current) => ({ ...current, window_days: days }))}
                />
                The last {days} days, by whole days
              </label>
            ))}
          </fieldset>
          <fieldset className="flex flex-col gap-1 text-sm">
            <legend className="font-medium text-ink">{SAMPLING_QUESTION}</legend>
            {(["no", "yes", "not_sure"] as const satisfies readonly Sampling[]).map((answer) => (
              <label key={answer} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="upstream_sampling"
                  checked={draft.upstream_sampling === answer}
                  onChange={() =>
                    setDraft((current) => ({ ...current, upstream_sampling: answer }))
                  }
                />
                {answer === "no" ? "No" : answer === "yes" ? "Yes" : "Not sure"}
              </label>
            ))}
            {draft.upstream_sampling === "yes" ? (
              <p className="max-w-prose text-warning-text">{SAMPLING_YES}</p>
            ) : null}
            {draft.upstream_sampling === "not_sure" ? (
              <p className="max-w-prose text-ink-dim">{SAMPLING_NOT_SURE}</p>
            ) : null}
          </fieldset>
          <TextField
            label="Name"
            name="name"
            value={draft.name}
            onChange={set("name")}
            error={errors.name}
            required
          />
        </div>
        <aside aria-label="What counts" className="flex flex-col gap-2 text-sm">
          <h2 className="font-semibold text-ink">What counts</h2>
          <ul className="flex max-w-prose list-disc flex-col gap-1 pl-5 text-ink-dim">
            {AVAILABILITY_DEFINITION.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </aside>
      </div>
      <div>
        <Button type="submit" pending={pending} pendingLabel="Saving">
          Save
        </Button>
      </div>
    </form>
  );
}

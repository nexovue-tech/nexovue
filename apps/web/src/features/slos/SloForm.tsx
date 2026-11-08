import { useNavigate, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { Permitted } from "@/shared/components/Permitted";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { useProjectSelection } from "@/shared/projects";
import { setupDocUrl } from "@/shared/setup-docs";

import type { Sampling, SloDraft, SloKind, SloView } from "./api";
import {
  bodyOf,
  REQUEST_KINDS,
  useChangeSlo,
  useCheckChoices,
  useCreateSlo,
  useSilenceCovered,
  useSlo,
  useSuggestion,
} from "./api";
import { LeftOutEndpoints, StopsCompletely, SuggestionPanel } from "./SloFormParts";
import {
  aboveHighest,
  belowLowest,
  boundaryPreview,
  DEFINITIONS,
  FAST_FAILURE_LINE,
  generatedName,
  highestBoundaryLine,
  SAMPLING_NOT_SURE,
  SAMPLING_QUESTION,
  SAMPLING_YES,
  snappedBoundary,
} from "./slo-words";

const WHO_MAY = "an owner, an admin or a member";
const FIELDS = [
  "name",
  "service_name",
  "endpoint_name",
  "check_id",
  "asked_ms",
  "left_out_endpoints",
  "objective_percent",
  "upstream_sampling",
] as const;
type Field = (typeof FIELDS)[number];
type Errors = Partial<Record<Field | "form", string>>;
/** FR5: where "Not sure" sends a person to check. */
const SAMPLING_DOC = `${setupDocUrl("collector")}#do-not-sample-in-the-collector`;
/** A typed threshold read back may sit a hair above its own boundary. */
const BOUNDARY_TOLERANCE = 1e-9;

/** What a person picks first: a service, an endpoint in it, or a check (design, step 1). */
type Watch = "service" | "endpoint" | "check";
const WATCH_CHOICES: ReadonlyArray<{ value: Watch; label: string }> = [
  { value: "service", label: "A service" },
  { value: "endpoint", label: "An endpoint" },
  { value: "check", label: "A check" },
];
const watchOf = (kind: SloKind): Watch =>
  kind === "check_availability"
    ? "check"
    : kind === "endpoint_availability"
      ? "endpoint"
      : "service";
const KIND_OF_WATCH: Record<Watch, SloKind> = {
  service: "service_availability",
  endpoint: "endpoint_availability",
  check: "check_availability",
};

const EMPTY: SloDraft = {
  kind: "service_availability",
  name: "",
  description: "",
  service_name: "",
  endpoint_name: "",
  check_id: "",
  asked_ms: "",
  left_out_endpoints: [],
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
      editing={false}
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
      editing
      pending={change.isPending}
      save={(draft) => change.mutateAsync(bodyOf(draft, { withKind: false }))}
    />
  );
}

function draftOf(view: SloView): SloDraft {
  return {
    kind: view.kind,
    name: view.name,
    description: view.description,
    service_name: view.service_name ?? "",
    endpoint_name: view.endpoint_name ?? "",
    check_id: view.check_id ?? "",
    asked_ms: view.asked_ms === null ? "" : String(view.asked_ms),
    left_out_endpoints: view.left_out_endpoints,
    objective_percent: String(Number(view.objective_percent)),
    window_days: view.window_days === 7 ? 7 : 28,
    upstream_sampling: view.upstream_sampling ?? "no",
  };
}

/** What the browser can say before asking (FR2, FR3); the API says the rest beside its field. */
function checked(draft: SloDraft, boundaries: readonly number[]): Errors {
  const errors: Errors = {};
  if (!draft.name.trim()) errors.name = "Give the target a name.";
  if (draft.kind === "check_availability") {
    if (!draft.check_id) errors.check_id = "Choose the check to count.";
  } else if (!draft.service_name.trim()) {
    errors.service_name = "Name the service to count.";
  }
  if (draft.kind === "endpoint_availability" && !draft.endpoint_name.trim()) {
    errors.endpoint_name = "Name the endpoint to count.";
  }
  if (draft.kind === "service_latency") {
    const asked = Number(draft.asked_ms);
    const lowest = boundaries[0];
    const highest = boundaries[boundaries.length - 1];
    if (!draft.asked_ms.trim() || !Number.isFinite(asked) || asked <= 0) {
      errors.asked_ms = "Give the response time a request must be at or under, in ms.";
    } else if (highest !== undefined && asked > highest * (1 + BOUNDARY_TOLERANCE)) {
      errors.asked_ms = aboveHighest(highest);
    } else if (lowest !== undefined && asked < lowest) {
      errors.asked_ms = belowLowest(lowest);
    }
  }
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
  const field = String(error.problem?.field ?? "");
  const detail = error.problem?.detail ?? error.message;
  const named = FIELDS.find((candidate) => candidate === field);
  return named ? { [named]: detail } : { form: detail };
}

/**
 * A field the person has not touched follows the suggestion and the source: the
 * objective, the threshold, the endpoints left out and the name. Once touched, it is
 * theirs. On edit, everything starts as theirs.
 */
type Touched = Record<"name" | "objective" | "asked" | "leftOut", boolean>;

function SloForm({
  title,
  initial,
  editing,
  pending,
  save,
}: {
  title: string;
  initial: SloDraft;
  editing: boolean;
  pending: boolean;
  save: (draft: SloDraft) => Promise<SloView>;
}) {
  const navigate = useNavigate();
  const { projectId } = useProjectSelection();
  const [draft, setDraft] = useState(initial);
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState<Touched>({
    name: editing,
    objective: editing,
    asked: editing,
    leftOut: editing,
  });
  // The source as it was when its field was left: one history read, not one a keystroke.
  const [asked, setAsked] = useState({
    service: initial.service_name,
    endpoint: initial.endpoint_name,
  });
  const watch = watchOf(draft.kind);
  const requestKind = REQUEST_KINDS.includes(draft.kind);
  const checks = useCheckChoices(watch === "check" ? projectId : undefined);
  const suggestion = useSuggestion({
    projectId: projectId ?? "",
    kind: draft.kind,
    service: asked.service.trim(),
    endpoint: asked.endpoint.trim(),
    checkId: draft.check_id,
  });
  const covered = useSilenceCovered(requestKind ? projectId : undefined, asked.service.trim());
  const read = suggestion.data;
  const boundaries = read?.boundaries_ms ?? [];
  const check = checks.data?.find((candidate) => candidate.id === draft.check_id);

  const followed: SloDraft = {
    ...draft,
    objective_percent:
      touched.objective || !read?.objective
        ? draft.objective_percent
        : String(Number(read.objective)),
    asked_ms:
      touched.asked || read?.boundary_ms == null ? draft.asked_ms : read.boundary_ms.toFixed(1),
    left_out_endpoints:
      touched.leftOut || !read
        ? draft.left_out_endpoints
        : read.endpoints.filter((each) => each.health_check).map((each) => each.name),
  };
  const boundary =
    draft.kind === "service_latency"
      ? snappedBoundary(Number(followed.asked_ms), boundaries)
      : null;
  const shown: SloDraft = {
    ...followed,
    name: touched.name
      ? draft.name
      : generatedName({
          kind: draft.kind,
          service: draft.service_name.trim(),
          endpoint: draft.endpoint_name.trim(),
          checkName: check?.name ?? "",
          boundaryMs: boundary,
        }),
  };

  const dirty = JSON.stringify(shown) !== JSON.stringify(initial);
  const guard = useUnsavedChanges(dirty && !pending);
  const set = (field: keyof SloDraft, touches?: keyof Touched) => (value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    if (touches) setTouched((current) => ({ ...current, [touches]: true }));
  };
  const pick = (changes: Partial<SloDraft>) => setDraft((current) => ({ ...current, ...changes }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = checked(shown, boundaries);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    try {
      const saved = await save({ ...shown, objective_percent: shown.objective_percent.trim() });
      setDraft(saved ? draftOf(saved) : shown);
      setTouched({ name: true, objective: true, asked: true, leftOut: true });
      await navigate({ to: "/slos/$sloId", params: { sloId: saved.id } });
    } catch (error) {
      setErrors(refusalOf(error));
    }
  }

  const summary = Object.values(errors).filter(Boolean);
  const leaveSource = () =>
    setAsked({ service: draft.service_name, endpoint: draft.endpoint_name });
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
          {editing ? null : (
            <Choices
              legend="What to watch"
              name="watch"
              choices={WATCH_CHOICES}
              value={watch}
              onChange={(value) => pick({ kind: KIND_OF_WATCH[value as Watch] })}
            />
          )}
          {watch === "check" ? (
            <CheckPicker
              checks={checks.data ?? []}
              loading={checks.isPending}
              value={draft.check_id}
              error={errors.check_id}
              onChange={(checkId) => pick({ check_id: checkId })}
            />
          ) : (
            // Leaving a source field asks for its history; a field's focus leaving bubbles here.
            <fieldset className="flex min-w-0 flex-col gap-4" onBlur={leaveSource}>
              <legend className="sr-only">Source</legend>
              <TextField
                label="Service"
                name="service_name"
                value={draft.service_name}
                onChange={set("service_name")}
                error={errors.service_name}
              />
              {watch === "endpoint" ? (
                <TextField
                  label="Endpoint"
                  name="endpoint_name"
                  value={draft.endpoint_name}
                  onChange={set("endpoint_name")}
                  error={errors.endpoint_name}
                  hint="As its spans name it, such as GET /checkout."
                />
              ) : null}
            </fieldset>
          )}
          {watch === "service" && !editing ? (
            <Choices
              legend="Kind"
              name="kind"
              choices={[
                { value: "service_availability", label: "Availability" },
                { value: "service_latency", label: "Latency" },
              ]}
              value={draft.kind}
              onChange={(value) => pick({ kind: value as SloKind })}
            />
          ) : null}
          {draft.kind === "service_latency" ? (
            <div className="flex flex-col gap-1 text-sm">
              <TextField
                label="Response time, in ms"
                name="asked_ms"
                value={shown.asked_ms}
                onChange={set("asked_ms", "asked")}
                error={errors.asked_ms}
              />
              {boundary !== null ? <p className="text-ink">{boundaryPreview(boundary)}</p> : null}
              <p className="max-w-prose text-ink-dim">{FAST_FAILURE_LINE}</p>
              {read ? (
                <p className="text-ink-dim">{highestBoundaryLine(read.highest_boundary_ms)}</p>
              ) : null}
            </div>
          ) : null}
          <TextField
            label="Target, in %"
            name="objective_percent"
            value={shown.objective_percent}
            onChange={set("objective_percent", "objective")}
            error={errors.objective_percent}
            hint="Between 90% and 99.99%, to two decimals."
          />
          <Choices
            legend="Window"
            name="window_days"
            choices={[
              { value: "28", label: "The last 28 days, by whole days" },
              { value: "7", label: "The last 7 days, by whole days" },
            ]}
            value={String(draft.window_days)}
            onChange={(value) => pick({ window_days: value === "7" ? 7 : 28 })}
          />
          {draft.kind === "service_availability" && read && read.endpoints.length > 0 ? (
            <LeftOutEndpoints
              endpoints={read.endpoints}
              chosen={shown.left_out_endpoints}
              error={errors.left_out_endpoints}
              onChange={(names) => {
                pick({ left_out_endpoints: names });
                setTouched((current) => ({ ...current, leftOut: true }));
              }}
            />
          ) : null}
          {requestKind ? (
            <SamplingQuestion
              value={draft.upstream_sampling}
              onChange={(answer) => pick({ upstream_sampling: answer })}
            />
          ) : null}
          <TextField
            label="Name"
            name="name"
            value={shown.name}
            onChange={set("name", "name")}
            error={errors.name}
          />
        </div>
        <aside aria-label="What counts" className="flex flex-col gap-4 text-sm">
          <SuggestionPanel
            suggestion={suggestion}
            kind={draft.kind}
            objective={shown.objective_percent}
            windowDays={draft.window_days}
            onCheckTarget={() => pick({ kind: "check_availability" })}
          />
          {requestKind && covered.data === false ? (
            <StopsCompletely service={asked.service.trim()} />
          ) : null}
          <div className="flex flex-col gap-2">
            <h2 className="font-semibold text-ink">What counts</h2>
            <ul className="flex max-w-prose list-disc flex-col gap-1 pl-5 text-ink-dim">
              {DEFINITIONS[draft.kind].map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
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

function Choices({
  legend,
  name,
  choices,
  value,
  onChange,
}: {
  legend: string;
  name: string;
  choices: ReadonlyArray<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-1 text-sm">
      <legend className="font-medium text-ink">{legend}</legend>
      {choices.map((choice) => (
        <label key={choice.value} className="flex items-center gap-2">
          <input
            type="radio"
            name={name}
            value={choice.value}
            checked={value === choice.value}
            onChange={() => onChange(choice.value)}
          />
          {choice.label}
        </label>
      ))}
    </fieldset>
  );
}

function CheckPicker({
  checks,
  loading,
  value,
  error,
  onChange,
}: {
  checks: ReadonlyArray<{ id: string; name: string }>;
  loading: boolean;
  value: string;
  error: string | undefined;
  onChange: (checkId: string) => void;
}) {
  if (loading) return <SkeletonRows rows={1} label="Loading the checks" />;
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor="slo-check"
        className="font-mono text-2xs text-ink-dim uppercase tracking-caps"
      >
        Check
      </label>
      <select
        id="slo-check"
        name="check_id"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? "slo-check-error" : undefined}
        className="w-full rounded-sm border border-line bg-surface px-2.5 py-1.5 text-base text-ink"
      >
        <option value="">Choose a check</option>
        {checks.map((choice) => (
          <option key={choice.id} value={choice.id}>
            {choice.name}
          </option>
        ))}
      </select>
      {error ? (
        <p id="slo-check-error" className="text-critical-text text-xs">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function SamplingQuestion({
  value,
  onChange,
}: {
  value: Sampling;
  onChange: (answer: Sampling) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-1 text-sm">
      <legend className="font-medium text-ink">{SAMPLING_QUESTION}</legend>
      {(["no", "yes", "not_sure"] as const satisfies readonly Sampling[]).map((answer) => (
        <label key={answer} className="flex items-center gap-2">
          <input
            type="radio"
            name="upstream_sampling"
            checked={value === answer}
            onChange={() => onChange(answer)}
          />
          {answer === "no" ? "No" : answer === "yes" ? "Yes" : "Not sure"}
        </label>
      ))}
      {value === "yes" ? <p className="max-w-prose text-warning-text">{SAMPLING_YES}</p> : null}
      {value === "not_sure" ? (
        <p className="max-w-prose text-ink-dim">
          {SAMPLING_NOT_SURE}{" "}
          <a href={SAMPLING_DOC} className="font-medium text-brand-text hover:underline">
            How to check
          </a>
        </p>
      ) : null}
    </fieldset>
  );
}

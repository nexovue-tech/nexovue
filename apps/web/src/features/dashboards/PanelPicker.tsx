import { useEffect, useId, useRef, useState } from "react";

import { ApiError, isInvalid, isRateLimited } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import type { WindowChoice } from "@/shared/selection";

import type { Definition, NodeChoice, PanelResult, PreviewAsk, Signal, StoredPanel } from "./api";
import { useNodeChoices, usePanelPreview, useServiceChoices } from "./api";
import { NotePanel } from "./NotePanel";
import { PanelWords } from "./PanelFrame";
import type { NodeRef } from "./panel-words";
import { nodeName, nodeOf, signalOf, stateWords, TIME_KINDS } from "./panel-words";
import { TimePanel } from "./TimePanel";

/**
 * What a panel shows, chosen in the side sheet (F20, T3.3; FR23, FR24).
 *
 * **Real values, raw behind escaped labels.** A service list is a native `<select>`: each
 * option shows its name through `visibleEscapes` and holds the raw name as its value. A
 * `<datalist>` cannot do this, since by the HTML standard an option's value is both what
 * the input shows and what it submits (read from the standard, not run; T3.3's phone check
 * is the founder's). A separate "Type a name" field takes a service that has not reported
 * yet, and keeps working when the list could not be read (AC134).
 *
 * **Apply reads once.** Each Apply is one charged read of this one panel, inside the
 * sheet, with the live window; typing reads nothing (AC135).
 *
 * Step 4 adds the top-N chart and one machine's metric; checks, statements and the rest
 * join with Step 5's kinds.
 */

type Kind = "project_trend" | "service_trend" | "top_services_trend" | "node_trend" | "note";
type Order = "worst" | "slowest";

const KINDS: ReadonlySet<string> = new Set<Kind>([
  "project_trend",
  "service_trend",
  "top_services_trend",
  "node_trend",
  "note",
]);

const KIND_GROUPS: readonly { question: string; kinds: readonly { id: Kind; label: string }[] }[] =
  [
    {
      question: "How is the project doing",
      kinds: [
        { id: "project_trend", label: "The whole project over time" },
        { id: "top_services_trend", label: "The worst or slowest services over time" },
      ],
    },
    {
      question: "How is a service doing",
      kinds: [{ id: "service_trend", label: "One service over time" }],
    },
    {
      question: "Machines",
      kinds: [{ id: "node_trend", label: "One machine's metric over time" }],
    },
    { question: "Text", kinds: [{ id: "note", label: "A note" }] },
  ];

const SIGNALS: readonly { id: Signal; label: string }[] = [
  { id: "rate", label: "Rate" },
  { id: "errors", label: "Errors" },
  { id: "latency", label: "Latency" },
];

const ORDERS: readonly { id: Order; label: string }[] = [
  { id: "worst", label: "Worst first: most failures" },
  { id: "slowest", label: "Slowest first" },
];

/** A top-N chart draws one to five lines (FR6). */
const COUNTS = [1, 2, 3, 4, 5].map((count) => ({ id: String(count), label: String(count) }));
const MOST_LINES = "5";

/** No service chosen in the list: the typed field, or nothing yet. */
const NONE = "";
const DEFAULT_WIDTH = 4;
const NOTE_MAX_LENGTH = 2_000;
const LABEL_MAX_LENGTH = 120;

export interface PreviewFit {
  /** The width to draw at, in px, when the panel's real width fits the sheet. */
  width?: number;
  narrower: boolean;
}

/**
 * Draw a preview at the panel's real width when it fits, and say so when it cannot
 * (AC112). An unmeasured width (0) is no claim either way.
 */
export function previewFit(realWidth: number, available: number): PreviewFit {
  if (realWidth <= 0 || available <= 0) return { narrower: false };
  if (realWidth <= available) return { width: realWidth, narrower: false };
  return { narrower: true };
}

/** True when a value holds characters a reader cannot see (FR61). */
export function holdsHiddenCharacters(value: string): boolean {
  return visibleEscapes(value) !== value;
}

interface Form {
  kind: Kind;
  signal: Signal;
  order: Order;
  count: string;
  /** The chosen node, as its key; NONE until one is chosen. */
  node: string;
  metric: string;
  chosen: string;
  typed: string;
  followsPicker: boolean;
  text: string;
  title: string;
}

function formOf(panel: StoredPanel | undefined): Form {
  const service = panel?.settings.service;
  return {
    kind: panel && isKind(panel.kind) ? panel.kind : "service_trend",
    signal: (panel && signalOf(panel)) ?? "rate",
    order: panel?.settings.order === "slowest" ? "slowest" : "worst",
    count: typeof panel?.settings.count === "number" ? String(panel.settings.count) : MOST_LINES,
    node: panel && nodeOf(panel) ? nodeKey(nodeOf(panel) as NodeRef) : NONE,
    metric: typeof panel?.settings.metric === "string" ? panel.settings.metric : NONE,
    chosen: typeof service === "string" ? service : NONE,
    typed: "",
    followsPicker: typeof service === "object" && service !== null,
    text: typeof panel?.settings.text === "string" ? panel.settings.text : "",
    title: panel?.title ?? "",
  };
}

function isKind(kind: string): kind is Kind {
  return KINDS.has(kind);
}

/** One node's identity as a select's value: all four parts, so two hosts never collide. */
export function nodeKey(node: NodeRef): string {
  return JSON.stringify([node.host, node.container, node.reporter, node.reporter_instance]);
}

function nodeFromKey(key: string): NodeRef | undefined {
  try {
    const [host, container, reporter, reporter_instance] = JSON.parse(key) as string[];
    return {
      host: host ?? "",
      container: container ?? "",
      reporter: reporter ?? "",
      reporter_instance: reporter_instance ?? "",
    };
  } catch {
    return undefined;
  }
}

/** The panel the form describes, or the sentence saying what is missing. */
function panelOf(
  form: Form,
  id: string,
  initial: StoredPanel | undefined,
): StoredPanel | { missing: string } {
  const base = {
    id,
    kind: form.kind,
    title: form.title.trim() === "" ? null : form.title.trim(),
    width: initial?.width ?? DEFAULT_WIDTH,
    height: initial && initial.kind === form.kind ? initial.height : ("medium" as const),
  };
  if (form.kind === "note") return { ...base, settings: { text: form.text } };
  if (form.kind === "project_trend") return { ...base, settings: { signal: form.signal } };
  if (form.kind === "top_services_trend") {
    return {
      ...base,
      settings: { signal: form.signal, order: form.order, count: Number(form.count) },
    };
  }
  if (form.kind === "node_trend") {
    const node = form.node === NONE ? undefined : nodeFromKey(form.node);
    if (node === undefined) return { missing: "Choose a machine." };
    if (form.metric === NONE) return { missing: "Choose what to show for it." };
    return { ...base, settings: { node, metric: form.metric } };
  }
  const service = serviceOf(form);
  if (service === undefined) return { missing: "Choose a service, or type its name." };
  return { ...base, settings: { service, signal: form.signal } };
}

function serviceOf(form: Form): string | { picker: true } | undefined {
  if (form.followsPicker) return { picker: true };
  if (form.typed.trim() !== "") return form.typed.trim();
  return form.chosen === NONE ? undefined : form.chosen;
}

/** The draft with this panel in its place, or added at the end. */
function definitionWith(definition: Definition, panel: StoredPanel): Definition {
  const at = definition.panels.findIndex((each) => each.id === panel.id);
  const panels =
    at === -1
      ? [...definition.panels, panel]
      : definition.panels.map((each, index) => (index === at ? panel : each));
  return { ...definition, panels };
}

export function PanelPicker({
  projectId,
  projectName,
  zone,
  choice,
  definition,
  panelId,
  initial,
  realWidth,
  onKeep,
}: {
  projectId: string | undefined;
  projectName: string;
  /** The project's clock, as every time on a dashboard is (FR46). */
  zone: string;
  choice: WindowChoice;
  /** The draft as it stands: the preview reads this panel inside it (FR7 applies whole). */
  definition: Definition;
  panelId: string;
  /** The panel being edited, or undefined for a new one. */
  initial?: StoredPanel;
  /** The panel's width on the page, in px, when it could be measured. */
  realWidth: number;
  onKeep: (panel: StoredPanel, reading: PanelResult | undefined) => void;
}) {
  const [form, setForm] = useState<Form>(() => formOf(initial));
  const [ask, setAsk] = useState<PreviewAsk | undefined>();
  const [applied, setApplied] = useState<StoredPanel | undefined>();
  const [problem, setProblem] = useState<string | undefined>();
  const services = useServiceChoices(form.kind === "service_trend" ? projectId : undefined, choice);
  const nodes = useNodeChoices(form.kind === "node_trend" ? projectId : undefined, choice);
  const preview = usePanelPreview(ask, projectId, choice, undefined);
  const update = (changes: Partial<Form>) => setForm((current) => ({ ...current, ...changes }));
  const candidate = panelOf(form, panelId, initial);
  const kindName = useId();

  const apply = () => {
    if ("missing" in candidate) {
      setProblem(candidate.missing);
      return;
    }
    setProblem(undefined);
    setApplied(candidate);
    // A note reads nothing: it is drawn from its own text.
    if (candidate.kind === "note") return;
    setAsk((current) => ({
      definition: definitionWith(definition, candidate),
      panelIds: [candidate.id],
      applied: (current?.applied ?? 0) + 1,
    }));
  };

  const keep = () => {
    if ("missing" in candidate) {
      setProblem(candidate.missing);
      return;
    }
    const same = applied !== undefined && JSON.stringify(applied) === JSON.stringify(candidate);
    onKeep(candidate, same ? preview.data?.panels[candidate.id] : undefined);
  };

  return (
    <div className="flex flex-col gap-4">
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 font-medium text-ink text-sm">What it shows</legend>
        {KIND_GROUPS.map((group) => (
          <div key={group.question} className="flex flex-col gap-1">
            <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
              {group.question}
            </p>
            {group.kinds.map((kind, index) => (
              <label key={kind.id} className="flex min-h-11 items-center gap-2 text-sm md:min-h-0">
                <input
                  type="radio"
                  name={kindName}
                  value={kind.id}
                  checked={form.kind === kind.id}
                  onChange={() => update({ kind: kind.id })}
                  data-autofocus={index === 0 && group === KIND_GROUPS[0] ? true : undefined}
                />
                {kind.label}
              </label>
            ))}
          </div>
        ))}
      </fieldset>

      <div className="flex flex-col gap-3">
        {form.kind === "service_trend" ? (
          <ServiceChoice
            form={form}
            update={update}
            pending={services.isPending}
            failed={services.isError}
            names={services.data}
            onRetry={() => void services.refetch()}
          />
        ) : null}
        {form.kind === "node_trend" ? (
          <NodeChoiceFields
            form={form}
            update={update}
            pending={nodes.isPending}
            failed={nodes.isError}
            nodes={nodes.data}
            onRetry={() => void nodes.refetch()}
          />
        ) : null}
        {form.kind === "top_services_trend" ? (
          <>
            <SelectField
              label="Chooses"
              value={form.order}
              options={ORDERS}
              optionLabel={(order) => order.label}
              onChange={(order) => update({ order: order as Order })}
              stacked
            />
            <SelectField
              label="How many services"
              value={form.count}
              options={COUNTS}
              optionLabel={(count) => count.label}
              onChange={(count) => update({ count })}
              stacked
            />
          </>
        ) : null}
        {form.kind === "note" ? (
          <NoteText value={form.text} onChange={(text) => update({ text })} />
        ) : form.kind === "node_trend" ? null : (
          <SelectField
            label="Shows"
            value={form.signal}
            options={SIGNALS}
            optionLabel={(signal) => signal.label}
            onChange={(signal) => update({ signal: signal as Signal })}
            stacked
          />
        )}
        <TextField
          label="Title (optional)"
          name="panel-title"
          value={form.title}
          onChange={(title) => update({ title: title.slice(0, LABEL_MAX_LENGTH) })}
          hint="Left empty, the panel is named for what it shows."
          required={false}
        />
      </div>

      {problem ? (
        <p role="alert" className="text-critical-text text-sm">
          {problem}
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={apply} className="min-h-11 md:min-h-0">
          Apply
        </Button>
        <Button onClick={keep} className="min-h-11 md:min-h-0">
          {initial ? "Keep changes" : "Add to dashboard"}
        </Button>
      </div>

      <section aria-label="Preview" className="flex flex-col gap-2">
        <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Preview</h3>
        <Preview
          applied={applied}
          pending={ask !== undefined && preview.isFetching}
          error={preview.isError ? preview.error : undefined}
          result={applied ? preview.data?.panels[applied.id] : undefined}
          correlationId={preview.data?.correlation_id}
          projectName={projectName}
          zone={zone}
          realWidth={realWidth}
          onReadAgain={apply}
        />
      </section>
    </div>
  );
}

function ServiceChoice({
  form,
  update,
  pending,
  failed,
  names,
  onRetry,
}: {
  form: Form;
  update: (changes: Partial<Form>) => void;
  pending: boolean;
  failed: boolean;
  names: string[] | undefined;
  onRetry: () => void;
}) {
  const chosen = form.typed.trim() !== "" ? form.typed.trim() : form.chosen;
  const options = [
    { id: NONE, label: "Choose a service" },
    ...(names ?? []).map((name) => ({ id: name, label: visibleEscapes(name) })),
  ];
  // A stored name the list no longer holds stays choosable rather than vanishing.
  if (form.chosen !== NONE && !names?.includes(form.chosen)) {
    options.push({ id: form.chosen, label: visibleEscapes(form.chosen) });
  }

  return (
    <div className="flex flex-col gap-3">
      <label className="flex min-h-11 items-center gap-2 text-sm md:min-h-0">
        <input
          type="checkbox"
          checked={form.followsPicker}
          onChange={(event) => update({ followsPicker: event.target.checked })}
        />
        Follow the service picked on the page
      </label>
      {form.followsPicker ? null : (
        <>
          {pending ? (
            <SkeletonRows rows={2} label="Listing services" />
          ) : failed ? (
            <div className="flex flex-col items-start gap-1 text-sm">
              <p className="text-warning-text">Could not list services. Type a name instead.</p>
              <button type="button" onClick={onRetry} className="text-brand underline">
                Try again
              </button>
            </div>
          ) : names && names.length === 0 ? (
            <p className="text-ink-dim text-sm">No services have reported yet. Type a name.</p>
          ) : (
            <SelectField
              label="Service"
              value={form.chosen}
              options={options}
              optionLabel={(option) => option.label}
              onChange={(name) => update({ chosen: name })}
              stacked
            />
          )}
          <TextField
            label="Type a name"
            name="typed-service"
            value={form.typed}
            onChange={(typed) => update({ typed })}
            hint={
              form.typed === ""
                ? "For a service that has not reported yet."
                : `Reads as: ${visibleEscapes(form.typed)}`
            }
            required={false}
          />
          {chosen !== NONE && holdsHiddenCharacters(chosen) ? (
            <p role="alert" className="text-sm text-warning-text">
              This name holds characters that do not show on screen: {visibleEscapes(chosen)}. Check
              it is the service you mean.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

function NoteText({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        Note
      </label>
      <textarea
        id={id}
        value={value}
        maxLength={NOTE_MAX_LENGTH}
        rows={5}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-sm border border-line bg-surface px-2.5 py-1.5 text-base text-ink outline-none hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      />
      <p className="text-ink-dim text-xs">
        Everyone in this organization can read this. Do not paste secrets or personal details.
      </p>
    </div>
  );
}

function Preview(props: {
  applied: StoredPanel | undefined;
  pending: boolean;
  error: unknown;
  result: PanelResult | undefined;
  correlationId: string | null | undefined;
  projectName: string;
  zone: string;
  realWidth: number;
  onReadAgain: () => void;
}) {
  const area = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(0);
  // Measured once a result arrives: the sheet may have been resized since it opened.
  useEffect(() => {
    if (props.result) setAvailable(area.current?.clientWidth ?? 0);
  }, [props.result]);
  const fit = previewFit(props.realWidth, available);
  const drawn = props.result?.state === "ok";

  return (
    <div ref={area} className="flex flex-col gap-2">
      {drawn && fit.narrower ? (
        <p className="text-ink-dim text-xs">Shown narrower than on the page.</p>
      ) : null}
      <div style={drawn && fit.width ? { width: fit.width } : undefined}>
        <PreviewBody {...props} />
      </div>
    </div>
  );
}

function PreviewBody({
  applied,
  pending,
  error,
  result,
  correlationId,
  projectName,
  zone,
  onReadAgain,
}: {
  applied: StoredPanel | undefined;
  pending: boolean;
  error: unknown;
  result: PanelResult | undefined;
  correlationId: string | null | undefined;
  projectName: string;
  zone: string;
  onReadAgain: () => void;
}) {
  if (!applied) {
    return <p className="text-ink-dim text-sm">Apply to see this panel with the live window.</p>;
  }
  if (applied.kind === "note") {
    const text = typeof applied.settings.text === "string" ? applied.settings.text : "";
    return <NotePanel text={text} />;
  }
  if (pending) {
    return (
      <div
        aria-hidden
        className="animate-pulse rounded-xs bg-raised"
        style={{ height: CHART_HEIGHT }}
      />
    );
  }
  if (error instanceof ApiError) {
    // Apply stays available: a refusal is for now, and the draft is untouched.
    if (isRateLimited(error)) return <PanelWords words="Too many reads just now." />;
    if (isInvalid(error)) {
      return (
        <p className="text-critical-text text-sm">
          Could not preview: {visibleEscapes(error.problem?.detail ?? refusalOf(error))}
        </p>
      );
    }
    return (
      <PanelWords
        words="Could not read this. The draft is unaffected."
        correlationId={error.context.correlationId}
        onReadAgain={onReadAgain}
      />
    );
  }
  if (!result) return null;
  if (result.state === "ok" && TIME_KINDS.has(applied.kind)) {
    return (
      <>
        <TimePanel panel={applied} reading={result} zone={zone} description="A preview." />
        <p className="text-ink-dim text-xs">Applied</p>
      </>
    );
  }
  const failing = result.state === "failed" || result.state === "timed_out";
  return (
    <PanelWords
      words={
        stateWords(result, applied, { canEdit: true, owner: null, projectName, overLimit: 0 }) ?? ""
      }
      correlationId={failing ? correlationId : undefined}
      onReadAgain={failing ? onReadAgain : undefined}
    />
  );
}

/** A validation refusal's first reason, when it has no sentence of its own. */
function refusalOf(error: ApiError): string {
  const errors = error.problem?.errors;
  if (Array.isArray(errors) && errors.length > 0) {
    const [first] = errors as { field?: string; reason?: string }[];
    return first.reason ?? "the draft was refused";
  }
  return "the draft was refused";
}

/**
 * A machine and one of the metrics it sent, from the displayed project's real nodes.
 * Each label is escaped and each value is the node's whole identity (FR23, FR61).
 */
function NodeChoiceFields({
  form,
  update,
  pending,
  failed,
  nodes,
  onRetry,
}: {
  form: Form;
  update: (changes: Partial<Form>) => void;
  pending: boolean;
  failed: boolean;
  nodes: NodeChoice[] | undefined;
  onRetry: () => void;
}) {
  if (pending) return <SkeletonRows rows={2} label="Listing machines" />;
  if (failed) {
    return (
      <div className="flex flex-col items-start gap-1 text-sm">
        <p className="text-ink-dim">Could not list machines.</p>
        <button type="button" onClick={onRetry} className="text-brand underline">
          Read again
        </button>
      </div>
    );
  }
  if (!nodes || nodes.length === 0) {
    return <p className="text-ink-dim text-sm">No machine has reported in this window.</p>;
  }
  const options = [
    { id: NONE, label: "Choose a machine" },
    ...nodes.map((node) => ({ id: nodeKey(node), label: nodeName(node) })),
  ];
  const chosen = nodes.find((node) => nodeKey(node) === form.node);
  const metrics = [
    { id: NONE, label: "Choose a metric" },
    ...(chosen?.signals ?? []).map((metric) => ({ id: metric, label: visibleEscapes(metric) })),
  ];
  return (
    <>
      <SelectField
        label="Machine"
        value={form.node}
        options={options}
        optionLabel={(option) => option.label}
        onChange={(node) => update({ node, metric: NONE })}
        stacked
      />
      <SelectField
        label="Shows"
        value={form.metric}
        options={metrics}
        optionLabel={(option) => option.label}
        onChange={(metric) => update({ metric })}
        stacked
      />
    </>
  );
}

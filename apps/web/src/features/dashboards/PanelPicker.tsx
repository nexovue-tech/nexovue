import { useEffect, useId, useRef, useState } from "react";

import { ApiError, isInvalid, isRateLimited } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import type { WindowChoice } from "@/shared/selection";

import type {
  Definition,
  NodeChoice,
  PanelResult,
  PreviewAsk,
  Signal,
  StatementChoice,
  StoredPanel,
} from "./api";
import {
  useCheckChoices,
  useNodeChoices,
  usePanelPreview,
  useServiceChoices,
  useStatementChoices,
} from "./api";
import { NotePanel } from "./NotePanel";
import { PanelWords } from "./PanelFrame";
import { PanelReading } from "./PanelReading";
import type { NodeRef } from "./panel-words";
import { KNOWN_KINDS, MEASURE_NAMES, nodeName, nodeOf, signalOf, stateWords } from "./panel-words";

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
 * Step 4 added the top-N chart and one machine's metric; Step 5 adds every other kind:
 * endpoints, the top machines, checks and statements by their real names, numbers with
 * their comparison, open findings and the ranked tables.
 */

type Kind =
  | "project_trend"
  | "service_trend"
  | "top_services_trend"
  | "node_trend"
  | "top_nodes_trend"
  | "endpoint_trend"
  | "check_trend"
  | "statement_trend"
  | "project_number"
  | "service_number"
  | "open_findings"
  | "services_table"
  | "endpoints_table"
  | "dependencies_table"
  | "error_groups_table"
  | "database_statements_table"
  | "web_vitals_table"
  | "note";
type Order = "worst" | "slowest";

const KIND_GROUPS: readonly { question: string; kinds: readonly { id: Kind; label: string }[] }[] =
  [
    {
      question: "How is the project doing",
      kinds: [
        { id: "project_trend", label: "The whole project over time" },
        { id: "top_services_trend", label: "The worst or slowest services over time" },
        { id: "project_number", label: "One number for the whole project" },
        { id: "services_table", label: "The worst or slowest services, as a table" },
        { id: "dependencies_table", label: "The busiest or failing dependencies" },
      ],
    },
    {
      question: "How is a service doing",
      kinds: [
        { id: "service_trend", label: "One service over time" },
        { id: "endpoint_trend", label: "One endpoint over time" },
        { id: "service_number", label: "One number for a service" },
        { id: "endpoints_table", label: "The worst or slowest endpoints" },
        { id: "open_findings", label: "How many findings are open" },
      ],
    },
    {
      question: "Machines",
      kinds: [
        { id: "node_trend", label: "One machine's metric over time" },
        { id: "top_nodes_trend", label: "The machines highest on a metric" },
      ],
    },
    { question: "Checks", kinds: [{ id: "check_trend", label: "One check over time" }] },
    {
      question: "Errors",
      kinds: [{ id: "error_groups_table", label: "The most frequent errors" }],
    },
    {
      question: "Databases",
      kinds: [
        { id: "statement_trend", label: "One statement's cost over time" },
        { id: "database_statements_table", label: "The slowest statements" },
      ],
    },
    { question: "Browsers", kinds: [{ id: "web_vitals_table", label: "Core Web Vitals" }] },
    { question: "Text", kinds: [{ id: "note", label: "A note" }] },
  ];

const KINDS: ReadonlySet<string> = new Set(
  KIND_GROUPS.flatMap((group) => group.kinds.map((kind) => kind.id)),
);

/** Kinds reading one service, named or picked; those reading the project may name none. */
const NEEDS_SERVICE: ReadonlySet<Kind> = new Set([
  "service_trend",
  "endpoint_trend",
  "service_number",
]);
const MAY_NAME_SERVICE: ReadonlySet<Kind> = new Set([
  "open_findings",
  "endpoints_table",
  "error_groups_table",
]);
const SIGNAL_KINDS: ReadonlySet<Kind> = new Set([
  "project_trend",
  "service_trend",
  "top_services_trend",
  "endpoint_trend",
]);
const ORDER_KINDS: ReadonlySet<Kind> = new Set([
  "top_services_trend",
  "services_table",
  "endpoints_table",
]);
const ROW_KINDS: ReadonlySet<Kind> = new Set([
  "services_table",
  "endpoints_table",
  "dependencies_table",
  "error_groups_table",
  "database_statements_table",
]);

const CHECK_SIGNALS = [
  { id: "uptime", label: "Uptime" },
  { id: "duration", label: "Duration" },
];
const SPAN_KINDS = [
  { id: "SERVER", label: "A request it serves" },
  { id: "CONSUMER", label: "A message it consumes" },
];
const SERVICE_MEASURES = ["requests_per_minute", "error_rate", "p50_ms", "p95_ms", "p99_ms"];
const PROJECT_MEASURES = [...SERVICE_MEASURES, "service_count"];
/** A table holds 1 to 20 rows (FR6). */
const ROW_COUNTS = [5, 10, 20].map((count) => ({ id: String(count), label: String(count) }));
const DEFAULT_ROWS = "10";

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
  /** For a kind that may name a service: the whole project instead. */
  wholeProject: boolean;
  text: string;
  title: string;
  spanKind: string;
  endpoint: string;
  checkSignal: string;
  check: string;
  /** The chosen statement, as its key; NONE until one is chosen. */
  statement: string;
  label: string;
  measure: string;
  compare: boolean;
  rows: string;
  failingOnly: boolean;
  deviceClass: string;
}

function setting(panel: StoredPanel | undefined, name: string): string {
  const value = panel?.settings[name];
  return typeof value === "string" ? value : NONE;
}

function formOf(panel: StoredPanel | undefined): Form {
  const service = panel?.settings.service;
  return {
    kind: panel && isKind(panel.kind) ? panel.kind : "service_trend",
    signal: (panel && signalOf(panel)) ?? "rate",
    order: panel?.settings.order === "slowest" ? "slowest" : "worst",
    count: typeof panel?.settings.count === "number" ? String(panel.settings.count) : MOST_LINES,
    node: panel && nodeOf(panel) ? nodeKey(nodeOf(panel) as NodeRef) : NONE,
    metric: setting(panel, "metric"),
    chosen: typeof service === "string" ? service : NONE,
    typed: "",
    followsPicker: typeof service === "object" && service !== null,
    wholeProject: panel !== undefined && service === null,
    text: setting(panel, "text"),
    title: panel?.title ?? "",
    spanKind: setting(panel, "span_kind") || "SERVER",
    endpoint: setting(panel, "endpoint"),
    checkSignal: panel?.settings.signal === "duration" ? "duration" : "uptime",
    check: setting(panel, "check"),
    statement:
      panel?.kind === "statement_trend"
        ? statementKey({
            query_hash: setting(panel, "query_hash"),
            server: {
              address: setting(panel, "server_address"),
              port: Number(panel.settings.server_port) || 0,
            },
          })
        : NONE,
    label: setting(panel, "label"),
    measure: setting(panel, "measure") || "p95_ms",
    compare: panel?.settings.compare === true,
    rows: typeof panel?.settings.rows === "number" ? String(panel.settings.rows) : DEFAULT_ROWS,
    failingOnly: panel?.settings.failing_only === true,
    deviceClass: setting(panel, "device_class"),
  };
}

/** One statement's identity as a select's value: hash, server and port. */
export function statementKey(statement: Pick<StatementChoice, "query_hash" | "server">): string {
  return JSON.stringify([statement.query_hash, statement.server.address, statement.server.port]);
}

function statementFromKey(key: string): [string, string, number] | undefined {
  try {
    const [hash, address, port] = JSON.parse(key) as [string, string, number];
    return typeof hash === "string" && typeof address === "string" && typeof port === "number"
      ? [hash, address, port]
      : undefined;
  } catch {
    return undefined;
  }
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
  const settings = settingsOf(form);
  if (isMissing(settings)) return settings;
  return { ...base, settings };
}

function isMissing(value: Record<string, unknown>): value is { missing: string } {
  return Object.keys(value).length === 1 && typeof value.missing === "string";
}

/** The settings the spec's table allows for the form's kind, or what is missing (FR6). */
function settingsOf(form: Form): Record<string, unknown> {
  const service = serviceOf(form);
  const optionalService = form.wholeProject ? null : service;
  if (NEEDS_SERVICE.has(form.kind) && service === undefined) {
    return { missing: "Choose a service, or type its name." };
  }
  if (MAY_NAME_SERVICE.has(form.kind) && optionalService === undefined) {
    return { missing: "Choose a service, or the whole project." };
  }
  const rows = Number(form.rows);
  switch (form.kind) {
    case "note":
      return { text: form.text };
    case "project_trend":
      return { signal: form.signal };
    case "service_trend":
      return { service, signal: form.signal };
    case "top_services_trend":
      return { signal: form.signal, order: form.order, count: Number(form.count) };
    case "node_trend": {
      const node = form.node === NONE ? undefined : nodeFromKey(form.node);
      if (node === undefined) return { missing: "Choose a machine." };
      if (form.metric === NONE) return { missing: "Choose what to show for it." };
      return { node, metric: form.metric };
    }
    case "top_nodes_trend":
      if (form.metric === NONE) return { missing: "Choose a metric." };
      return { metric: form.metric, count: Number(form.count) };
    case "endpoint_trend":
      if (form.endpoint.trim() === "") return { missing: "Type the endpoint's name." };
      return {
        service,
        span_kind: form.spanKind,
        endpoint: form.endpoint.trim(),
        signal: form.signal,
      };
    case "check_trend":
      if (form.check.trim() === "") return { missing: "Choose a check, or type its name." };
      return { check: form.check.trim(), signal: form.checkSignal };
    case "statement_trend": {
      const statement = statementFromKey(form.statement);
      if (statement === undefined) return { missing: "Choose a statement." };
      const [query_hash, server_address, server_port] = statement;
      return {
        query_hash,
        server_address,
        server_port,
        label: form.label.trim() === "" ? null : form.label.trim(),
      };
    }
    case "project_number":
      return { measure: form.measure, compare: form.compare };
    case "service_number":
      return {
        service,
        measure: form.measure === "service_count" ? "p95_ms" : form.measure,
        compare: form.compare,
      };
    case "open_findings":
      return { service: optionalService };
    case "services_table":
      return { order: form.order, rows };
    case "endpoints_table":
      return { service: optionalService, order: form.order, rows };
    case "dependencies_table":
      return { failing_only: form.failingOnly, rows };
    case "error_groups_table":
      return { service: optionalService, rows };
    case "database_statements_table":
      return { rows };
    case "web_vitals_table":
      return { device_class: form.deviceClass.trim() === "" ? null : form.deviceClass.trim() };
  }
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
  const namesService = NEEDS_SERVICE.has(form.kind) || MAY_NAME_SERVICE.has(form.kind);
  const services = useServiceChoices(namesService ? projectId : undefined, choice);
  const machines = form.kind === "node_trend" || form.kind === "top_nodes_trend";
  const nodes = useNodeChoices(machines ? projectId : undefined, choice);
  const checks = useCheckChoices(form.kind === "check_trend" ? projectId : undefined);
  const statements = useStatementChoices(
    form.kind === "statement_trend" ? projectId : undefined,
    choice,
  );
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
    // A note and open findings read nothing: drawn from their text and the page's context.
    if (candidate.kind === "note" || candidate.kind === "open_findings") return;
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
        {MAY_NAME_SERVICE.has(form.kind) ? (
          <label className="flex min-h-11 items-center gap-2 text-sm md:min-h-0">
            <input
              type="checkbox"
              checked={form.wholeProject}
              onChange={(event) => update({ wholeProject: event.target.checked })}
            />
            The whole project
          </label>
        ) : null}
        {namesService && !(MAY_NAME_SERVICE.has(form.kind) && form.wholeProject) ? (
          <ServiceChoice
            form={form}
            update={update}
            pending={services.isPending}
            failed={services.isError}
            names={services.data}
            onRetry={() => void services.refetch()}
          />
        ) : null}
        <StepFiveFields
          form={form}
          update={update}
          checks={checks}
          statements={statements}
          metrics={[...new Set((nodes.data ?? []).flatMap((node) => node.signals))].sort()}
        />
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
        {ORDER_KINDS.has(form.kind) ? (
          <SelectField
            label="Chooses"
            value={form.order}
            options={ORDERS}
            optionLabel={(order) => order.label}
            onChange={(order) => update({ order: order as Order })}
            stacked
          />
        ) : null}
        {form.kind === "top_services_trend" || form.kind === "top_nodes_trend" ? (
          <SelectField
            label={form.kind === "top_nodes_trend" ? "How many machines" : "How many services"}
            value={form.count}
            options={COUNTS}
            optionLabel={(count) => count.label}
            onChange={(count) => update({ count })}
            stacked
          />
        ) : null}
        {ROW_KINDS.has(form.kind) ? (
          <SelectField
            label="Rows"
            value={form.rows}
            options={ROW_COUNTS}
            optionLabel={(count) => count.label}
            onChange={(rows) => update({ rows })}
            stacked
          />
        ) : null}
        {form.kind === "note" ? (
          <NoteText value={form.text} onChange={(text) => update({ text })} />
        ) : null}
        {SIGNAL_KINDS.has(form.kind) ? (
          <SelectField
            label="Shows"
            value={form.signal}
            options={SIGNALS}
            optionLabel={(signal) => signal.label}
            onChange={(signal) => update({ signal: signal as Signal })}
            stacked
          />
        ) : null}
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
  if (applied.kind === "open_findings") {
    return (
      <p className="text-ink-dim text-sm">
        Counts this project's open findings when the dashboard is open. It reads nothing.
      </p>
    );
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
  if (result.state === "ok" && KNOWN_KINDS.has(applied.kind)) {
    return (
      <>
        <PanelReading panel={applied} reading={result} zone={zone} description="A preview." />
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

/**
 * The settings Step 5's kinds add: an endpoint, a check and a statement by their real
 * names where the project lists them, a machine metric, a measure and its comparison, a
 * dependency filter and a device class (FR6, FR23). Labels are escaped and values raw.
 */
function StepFiveFields({
  form,
  update,
  checks,
  statements,
  metrics,
}: {
  form: Form;
  update: (changes: Partial<Form>) => void;
  checks: { isPending: boolean; isError: boolean; data: string[] | undefined };
  statements: { isPending: boolean; isError: boolean; data: StatementChoice[] | undefined };
  metrics: string[];
}) {
  switch (form.kind) {
    case "endpoint_trend":
      return (
        <>
          <SelectField
            label="Endpoint kind"
            value={form.spanKind}
            options={SPAN_KINDS}
            optionLabel={(kind) => kind.label}
            onChange={(spanKind) => update({ spanKind })}
            stacked
          />
          <TextField
            label="Endpoint"
            name="endpoint"
            value={form.endpoint}
            onChange={(endpoint) => update({ endpoint })}
            hint={
              form.endpoint === ""
                ? "As the services screen names it, such as GET /orders."
                : `Reads as: ${visibleEscapes(form.endpoint)}`
            }
            required={false}
          />
        </>
      );
    case "top_nodes_trend":
      return (
        <SelectField
          label="Metric"
          value={form.metric}
          options={[
            {
              id: NONE,
              label: metrics.length === 0 ? "No machine has reported" : "Choose a metric",
            },
            ...metrics.map((metric) => ({ id: metric, label: visibleEscapes(metric) })),
          ]}
          optionLabel={(option) => option.label}
          onChange={(metric) => update({ metric })}
          stacked
        />
      );
    case "check_trend":
      return (
        <>
          {checks.isPending ? (
            <SkeletonRows rows={1} label="Listing checks" />
          ) : checks.isError ? (
            <p className="text-sm text-warning-text">Could not list checks. Type a name instead.</p>
          ) : (
            <SelectField
              label="Check"
              value={(checks.data ?? []).includes(form.check) ? form.check : NONE}
              options={[
                { id: NONE, label: "Choose a check" },
                ...(checks.data ?? []).map((name) => ({ id: name, label: visibleEscapes(name) })),
              ]}
              optionLabel={(option) => option.label}
              onChange={(check) => update({ check })}
              stacked
            />
          )}
          <TextField
            label="Or type its name"
            name="typed-check"
            value={form.check}
            onChange={(check) => update({ check })}
            hint="Read by name in the displayed project, so one dashboard works on each."
            required={false}
          />
          <SelectField
            label="Shows"
            value={form.checkSignal}
            options={CHECK_SIGNALS}
            optionLabel={(signal) => signal.label}
            onChange={(checkSignal) => update({ checkSignal })}
            stacked
          />
        </>
      );
    case "statement_trend":
      return (
        <>
          {statements.isPending ? (
            <SkeletonRows rows={1} label="Listing statements" />
          ) : statements.isError ? (
            <p className="text-sm text-warning-text">Could not list statements.</p>
          ) : (
            <SelectField
              label="Statement"
              value={form.statement}
              options={[
                {
                  id: NONE,
                  label:
                    (statements.data ?? []).length === 0
                      ? "No statement reported in this window"
                      : "Choose a statement",
                },
                ...(statements.data ?? []).map((statement) => ({
                  id: statementKey(statement),
                  label: `${visibleEscapes(statement.query_hash)} on ${visibleEscapes(statement.server.address)}:${statement.server.port}`,
                })),
              ]}
              optionLabel={(option) => option.label}
              onChange={(statement) => update({ statement })}
              stacked
            />
          )}
          <TextField
            label="Label (optional)"
            name="statement-label"
            value={form.label}
            onChange={(label) => update({ label: label.slice(0, LABEL_MAX_LENGTH) })}
            hint="No statement text is kept, so name it for your team."
            required={false}
          />
        </>
      );
    case "project_number":
    case "service_number": {
      const measures = form.kind === "project_number" ? PROJECT_MEASURES : SERVICE_MEASURES;
      return (
        <>
          <SelectField
            label="Shows"
            value={measures.includes(form.measure) ? form.measure : "p95_ms"}
            options={measures.map((measure) => ({ id: measure, label: MEASURE_NAMES[measure] }))}
            optionLabel={(option) => option.label}
            onChange={(measure) => update({ measure })}
            stacked
          />
          <label className="flex min-h-11 items-center gap-2 text-sm md:min-h-0">
            <input
              type="checkbox"
              checked={form.compare}
              onChange={(event) => update({ compare: event.target.checked })}
            />
            Compare with the window before (two reads)
          </label>
        </>
      );
    }
    case "dependencies_table":
      return (
        <label className="flex min-h-11 items-center gap-2 text-sm md:min-h-0">
          <input
            type="checkbox"
            checked={form.failingOnly}
            onChange={(event) => update({ failingOnly: event.target.checked })}
          />
          Only dependencies that failed
        </label>
      );
    case "web_vitals_table":
      return (
        <TextField
          label="Device class (optional)"
          name="device-class"
          value={form.deviceClass}
          onChange={(deviceClass) => update({ deviceClass })}
          hint="Such as mobile or desktop; empty reads every device."
          required={false}
        />
      );
    default:
      return null;
  }
}

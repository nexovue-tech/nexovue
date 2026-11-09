import { useNavigate } from "@tanstack/react-router";
import { type FormEvent, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { flushSync } from "react-dom";

import { ApiError, isConflict, isNotFound } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { Permitted } from "@/shared/components/Permitted";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { useProjects } from "@/shared/projects";
import { useSelection } from "@/shared/selection";

import type { Dashboard, DashboardDocument, PanelResult, PreviewAsk, StoredPanel } from "./api";
import {
  useCreateDashboard,
  useDashboard,
  useDashboardContext,
  useDashboardTemplates,
  usePanelPreview,
  useReplaceDashboard,
} from "./api";
import { DashboardActions } from "./DashboardActions";
import type { DraftAction } from "./draft";
import {
  announce,
  blankDocument,
  draftOf,
  isDirty,
  MAX_PANELS,
  MAX_SCANS_PER_READ,
  newPanelId,
  panelName,
  reduceDraft,
  worstCaseReads,
} from "./draft";
import { gridSpans } from "./grid-spans";
import { LinksEditor } from "./LinksEditor";
import { NotePanel } from "./NotePanel";
import { PanelWords } from "./PanelFrame";
import { PanelPicker } from "./PanelPicker";
import type { ActionKey } from "./PanelToolbar";
import { PanelToolbar } from "./PanelToolbar";
import { signalOf, stateWords, TIME_KINDS } from "./panel-words";
import { SideSheet } from "./SideSheet";
import { TimePanel } from "./TimePanel";

/**
 * The builder (F20, Step 3): `/dashboards/new` and `/dashboards/$dashboardId/edit`.
 *
 * **The same grid, in place,** so a person edits what they will see (design, *The
 * builder*). The draft is read once on opening, as opening the dashboard would be, and
 * after that only on Apply in the side sheet: a move or a resize changes no number, and
 * each read is charged (AC135).
 *
 * **Nothing is written until Save** (AC38). Save names the version the draft was read at,
 * so a colleague's save in between is a conflict that keeps this draft on screen (FR19,
 * AC10). Leaving warns only while something differs from what is stored (FR20, AC133).
 * A viewer is refused the screen, never shown a form that fails (AC7).
 */

const WHO_MAY = "an owner, an admin or a member";
/** Tailwind's `lg` is 64rem: below it every panel is full width (T3.8). */
const NARROW_QUERY = "(max-width: 63.98rem)";
const COLUMNS = 12;
const COPY_SUFFIX = " (copy)";

const REFRESH_CHOICES = [
  { id: "30", label: "Every 30 seconds" },
  { id: "60", label: "Every minute" },
  { id: "300", label: "Every 5 minutes" },
  { id: "off", label: "Off" },
] as const;
const RANGE_CHOICES = [
  { id: "", label: "The reader's own window" },
  { id: "15m", label: "Last 15 minutes" },
  { id: "1h", label: "Last hour" },
  { id: "6h", label: "Last 6 hours" },
  { id: "24h", label: "Last 24 hours" },
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
] as const;

export function DashboardEditScreen({ dashboardId }: { dashboardId: string }) {
  return (
    <Permitted action="edit_dashboards" who={WHO_MAY}>
      <EditExisting dashboardId={dashboardId} />
    </Permitted>
  );
}

export function NewDashboardScreen({ templateId }: { templateId?: string }) {
  return (
    <Permitted action="edit_dashboards" who={WHO_MAY}>
      <NewFromTemplate templateId={templateId} />
    </Permitted>
  );
}

function EditExisting({ dashboardId }: { dashboardId: string }) {
  const dashboard = useDashboard(dashboardId);
  const stored = useMemo(
    () => (dashboard.data ? documentOf(dashboard.data) : undefined),
    [dashboard.data],
  );
  if (dashboard.isPending) return <SkeletonRows rows={6} label="Loading the dashboard" />;
  if (dashboard.isError || !stored) {
    return (
      <p className="p-6 text-ink">
        {isNotFound(dashboard.error)
          ? "This dashboard does not exist or is no longer available."
          : "Could not read this dashboard."}
      </p>
    );
  }
  // A new version (a reload after a conflict) starts a new draft from it.
  return (
    <Builder
      key={dashboard.data.version}
      stored={stored}
      saved={dashboard.data}
      onReload={() => void dashboard.refetch()}
    />
  );
}

function NewFromTemplate({ templateId }: { templateId?: string }) {
  const templates = useDashboardTemplates();
  const stored = useMemo(() => {
    if (templateId === undefined) return blankDocument();
    return templates.data?.find((template) => template.id === templateId)?.document;
  }, [templateId, templates.data]);
  if (templateId !== undefined && templates.isPending) {
    return <SkeletonRows rows={6} label="Opening the starting point" />;
  }
  // An unknown starting point opens a blank draft rather than nothing.
  return <Builder stored={stored ?? blankDocument()} />;
}

function documentOf(dashboard: Dashboard): DashboardDocument {
  return {
    title: dashboard.title,
    description: dashboard.description,
    definition: dashboard.definition,
  };
}

/** Whether the window is below `lg`, following it as it changes. */
function useNarrow(): boolean {
  return useSyncExternalStore(subscribeNarrow, readNarrow, () => false);
}

function subscribeNarrow(changed: () => void): () => void {
  try {
    const query = window.matchMedia(NARROW_QUERY);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  } catch {
    return () => {};
  }
}

function readNarrow(): boolean {
  try {
    return window.matchMedia(NARROW_QUERY).matches;
  } catch {
    return false;
  }
}

/** Where focus goes once a change has rendered (AC37, `EditableRows`' rule). */
type PendingFocus = { panelId: string; action?: ActionKey } | { addButton: true };

interface SheetState {
  panelId: string;
  /** The panel being edited; undefined for a new one. */
  panel?: StoredPanel;
  realWidth: number;
}

function Builder({
  stored,
  saved,
  onReload,
}: {
  stored: DashboardDocument;
  saved?: Dashboard;
  onReload?: () => void;
}) {
  const navigate = useNavigate();
  const { projectId, window: choice } = useSelection();
  const projects = useProjects();
  const context = useDashboardContext(saved, projectId, choice);
  const create = useCreateDashboard();
  const replace = useReplaceDashboard(saved?.id ?? "");
  const narrow = useNarrow();

  const [draft, setDraft] = useState(() => draftOf(stored));
  const [message, setMessage] = useState("");
  const [readings, setReadings] = useState<Record<string, PanelResult>>({});
  const [sheet, setSheet] = useState<SheetState | undefined>();
  const [problems, setProblems] = useState<string[]>([]);
  const [conflict, setConflict] = useState<string | undefined>();
  const [leaving, setLeaving] = useState(false);
  const pendingFocus = useRef<PendingFocus | null>(null);
  const grid = useRef<HTMLDivElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);

  const document_ = draft.document;
  const panels = document_.definition.panels;
  const reads = worstCaseReads(document_.definition);
  const dirty = isDirty(document_, stored);
  const guard = useUnsavedChanges(dirty && !leaving);
  const projectName =
    context.data?.project.name ??
    projects.data?.find((project) => project.id === projectId)?.name ??
    "this project";
  // A new dashboard has no context to name the project's clock: see `status.md`.
  const zone = context.data?.project.timezone;

  // Read once on opening, as opening the dashboard would be; never per change (AC135).
  const opening = useMemo<PreviewAsk | undefined>(
    () =>
      stored.definition.panels.some((panel) => panel.kind !== "note")
        ? { definition: stored.definition, panelIds: undefined, applied: 0 }
        : undefined,
    [stored],
  );
  const page = usePanelPreview(opening, projectId, choice, undefined);
  useEffect(() => {
    const answer = page.data;
    // What Apply read since is newer, so it wins.
    if (answer) setReadings((current) => ({ ...answer.panels, ...current }));
  }, [page.data]);

  useEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    pendingFocus.current = null;
    if ("addButton" in pending) {
      addButton.current?.focus();
      return;
    }
    const section = grid.current?.querySelector(`section[data-panel-id="${pending.panelId}"]`);
    const target =
      (pending.action
        ? section?.querySelector<HTMLElement>(`[data-action="${pending.action}"]`)
        : null) ?? section?.querySelector<HTMLElement>('[role="toolbar"] [tabindex="0"]');
    target?.focus();
  });

  const change = (action: DraftAction, focus: PendingFocus | null) => {
    const next = reduceDraft(draft, action);
    setDraft(next);
    setMessage(announce(action, draft, next));
    pendingFocus.current = focus;
    if (action.type === "duplicate" && next !== draft) {
      const copy = next.document.definition.panels.find(
        (panel) => !panels.some((each) => each.id === panel.id),
      );
      const reading = readings[action.id];
      if (copy && reading) setReadings((current) => ({ ...current, [copy.id]: reading }));
    }
  };

  const onPanelChange = (action: DraftAction, key: ActionKey) => {
    if (action.type === "remove") {
      const index = panels.findIndex((panel) => panel.id === action.id);
      const next = panels[index + 1];
      change(action, next ? { panelId: next.id } : { addButton: true });
      return;
    }
    if (action.type === "duplicate") {
      change(action, { panelId: newPanelId(panels) });
      return;
    }
    change(action, "id" in action ? { panelId: action.id, action: key } : null);
  };

  const openSheet = (panel?: StoredPanel) => {
    const width = panel?.width ?? 4;
    setSheet({
      panelId: panel?.id ?? newPanelId(panels),
      panel,
      realWidth: ((grid.current?.clientWidth ?? 0) * width) / COLUMNS,
    });
  };

  const keep = (panel: StoredPanel, reading: PanelResult | undefined) => {
    const exists = panels.some((each) => each.id === panel.id);
    change(exists ? { type: "replace", panel } : { type: "add", panel }, { panelId: panel.id });
    // A panel changed without Apply has no reading of what it now shows.
    setReadings((current) => {
      const next = { ...current };
      if (reading) next[panel.id] = reading;
      else delete next[panel.id];
      return next;
    });
    setSheet(undefined);
  };

  const leaveTo = async (to: () => Promise<void>) => {
    flushSync(() => setLeaving(true));
    await to();
  };

  const toSaved = (id: string) =>
    leaveTo(() => navigate({ to: "/dashboards/$dashboardId", params: { dashboardId: id } }));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const found = checkDraft(document_, reads);
    setProblems(found);
    setConflict(undefined);
    if (found.length > 0) return;
    const body = { ...document_, title: document_.title.trim() };
    try {
      const result = saved
        ? await replace.mutateAsync({ ...body, version: saved.version })
        : await create.mutateAsync(body);
      await toSaved(result.id);
    } catch (error) {
      if (isConflict(error) && saved) {
        setConflict(
          (error as ApiError).problem?.detail ??
            "Someone saved this dashboard since you opened it. Reload it, or save yours as a copy.",
        );
        return;
      }
      setProblems(refusals(error));
    }
  };

  const saveAsCopy = async () => {
    try {
      const copy = await create.mutateAsync({
        ...document_,
        title: `${document_.title.trim()}${COPY_SUFFIX}`,
      });
      await toSaved(copy.id);
    } catch (error) {
      setProblems(refusals(error));
    }
  };

  const cancel = () =>
    void (saved
      ? navigate({ to: "/dashboards/$dashboardId", params: { dashboardId: saved.id } })
      : navigate({ to: "/dashboards" }));

  const spans = gridSpans(panels);
  const atCap = panels.length >= MAX_PANELS;

  return (
    <form onSubmit={save} noValidate className="flex flex-col gap-4 p-4">
      <UnsavedChangesPrompt guard={guard} what="this dashboard" />
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="font-semibold text-ink text-lg">
            {saved ? `Editing: ${visibleEscapes(saved.title)}` : "New dashboard"}
          </h1>
          <p className="text-ink-dim text-sm">
            {panels.length} of {MAX_PANELS} panels · {reads} of {MAX_SCANS_PER_READ} reads
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={cancel} className="min-h-11 md:min-h-0">
            Cancel
          </Button>
          <Button
            type="submit"
            pending={create.isPending || replace.isPending}
            pendingLabel="Saving"
            className="min-h-11 md:min-h-0"
          >
            Save
          </Button>
        </div>
      </header>

      {conflict ? (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-sm border border-warning-border bg-warning-subtle p-3 text-sm text-warning-text"
        >
          <p>{conflict} Your changes are still here.</p>
          <p>Reloading shows their version and drops your changes. A copy keeps yours.</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => onReload?.()}>
              Reload it
            </Button>
            <Button variant="secondary" onClick={() => void saveAsCopy()}>
              Save as a copy
            </Button>
          </div>
        </div>
      ) : null}
      {problems.length > 0 ? (
        <div
          role="alert"
          data-summary
          className="rounded-sm border border-critical-border bg-critical-subtle p-3 text-critical-text text-sm"
        >
          <p className="font-medium">The dashboard was not saved:</p>
          <ul className="list-disc pl-5">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <DashboardFields document={document_} onChange={(action) => change(action, null)} />

      {page.isError ? (
        <p className="text-sm text-warning-text">
          Could not preview this draft just now. Edit a panel and Apply to read it.
        </p>
      ) : null}

      <div ref={grid} className="grid grid-cols-12 gap-3">
        {panels.map((panel) => (
          <section
            key={panel.id}
            data-panel-id={panel.id}
            aria-labelledby={`edit-panel-${panel.id}-title`}
            className={`col-span-12 flex flex-col gap-2 rounded-sm border border-line p-3 ${spans[panel.id] ?? ""}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <h2
                id={`edit-panel-${panel.id}-title`}
                className="font-mono text-2xs text-ink-dim uppercase tracking-caps"
              >
                {panelName(panel)}
              </h2>
              <PanelToolbar
                panel={panel}
                panels={panels}
                narrow={narrow}
                onChange={onPanelChange}
                onEdit={() => openSheet(panel)}
              />
            </div>
            <BuilderPanelBody
              panel={panel}
              reading={readings[panel.id]}
              readingPending={page.isFetching}
              projectName={projectName}
              zone={zone}
            />
          </section>
        ))}
      </div>

      {draft.removed ? (
        <p className="text-ink-dim text-sm">
          Removed {panelName(draft.removed.panel)}.{" "}
          <button
            type="button"
            onClick={() => {
              const restored = draft.removed?.panel.id;
              change({ type: "undo-remove" }, restored ? { panelId: restored } : null);
            }}
            className="min-h-11 text-brand underline md:min-h-0"
          >
            Undo
          </button>
        </p>
      ) : null}

      <div className="flex flex-col items-start gap-1">
        <button
          ref={addButton}
          type="button"
          aria-disabled={atCap ? true : undefined}
          onClick={() =>
            atCap
              ? setMessage(`A dashboard holds ${MAX_PANELS} panels. Split it into two.`)
              : openSheet()
          }
          className="min-h-11 rounded-sm border border-line px-3 py-1.5 text-ink text-sm hover:bg-hover aria-disabled:opacity-50 md:min-h-0"
        >
          Add a panel
        </button>
        {atCap ? (
          <p className="text-ink-dim text-sm">
            A dashboard holds {MAX_PANELS} panels, because each one reads the database. Split it
            into two.
          </p>
        ) : null}
        {reads > MAX_SCANS_PER_READ ? (
          <p className="text-sm text-warning-text">{tooManyReads(reads)}</p>
        ) : null}
      </div>

      {saved ? (
        <DashboardActions
          dashboard={saved}
          onDeleted={() => void leaveTo(() => navigate({ to: "/dashboards" }))}
        />
      ) : null}

      {/* The page's one live region: every change is said here, and only here (AC143). */}
      <p data-live-region role="status" aria-live="polite" className="sr-only">
        {message}
      </p>

      {sheet ? (
        <SideSheet
          title={sheet.panel ? `Edit ${panelName(sheet.panel)}` : "Add a panel"}
          onClose={() => setSheet(undefined)}
        >
          <PanelPicker
            projectId={projectId}
            projectName={projectName}
            zone={zone ?? "UTC"}
            choice={choice}
            definition={document_.definition}
            panelId={sheet.panelId}
            initial={sheet.panel}
            realWidth={sheet.realWidth}
            onKeep={keep}
          />
        </SideSheet>
      ) : null}
    </form>
  );
}

function DashboardFields({
  document,
  onChange,
}: {
  document: DashboardDocument;
  onChange: (action: DraftAction) => void;
}) {
  const definition = document.definition;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex flex-col gap-3">
        <TextField
          label="Title"
          name="title"
          value={document.title}
          onChange={(title) => onChange({ type: "set", changes: { title } })}
        />
        <TextField
          label="Description"
          name="description"
          value={document.description}
          onChange={(description) => onChange({ type: "set", changes: { description } })}
          required={false}
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <SelectField
            label="Refresh"
            value={definition.refresh_seconds === null ? "off" : String(definition.refresh_seconds)}
            options={REFRESH_CHOICES}
            optionLabel={(option) => option.label}
            onChange={(value) =>
              onChange({
                type: "settings",
                changes: { refresh_seconds: value === "off" ? null : refreshOf(value) },
              })
            }
            stacked
          />
          <SelectField
            label="Opens on"
            value={definition.default_range ?? ""}
            options={RANGE_CHOICES}
            optionLabel={(option) => option.label}
            onChange={(value) =>
              onChange({
                type: "settings",
                changes: { default_range: value === "" ? null : value },
              })
            }
            stacked
          />
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm md:min-h-0">
          <input
            type="checkbox"
            checked={definition.service_picker}
            onChange={(event) =>
              onChange({ type: "settings", changes: { service_picker: event.target.checked } })
            }
          />
          Offer a service picker, so one dashboard serves every service
        </label>
      </div>
      <LinksEditor
        links={definition.links}
        onChange={(links) => onChange({ type: "settings", changes: { links } })}
      />
    </div>
  );
}

function refreshOf(value: string): 30 | 60 | 300 {
  return value === "30" ? 30 : value === "300" ? 300 : 60;
}

function BuilderPanelBody({
  panel,
  reading,
  readingPending,
  projectName,
  zone,
}: {
  panel: StoredPanel;
  reading: PanelResult | undefined;
  readingPending: boolean;
  projectName: string;
  zone: string | undefined;
}) {
  if (panel.kind === "note") {
    return <NotePanel text={typeof panel.settings.text === "string" ? panel.settings.text : ""} />;
  }
  if (!TIME_KINDS.has(panel.kind)) {
    return (
      <PanelWords words="This panel type is no longer available. Remove it, or edit it to choose what it shows." />
    );
  }
  if (reading === undefined) {
    if (readingPending) {
      return (
        <div
          aria-hidden
          className="animate-pulse rounded-xs bg-raised"
          style={{ height: CHART_HEIGHT }}
        />
      );
    }
    return <PanelWords words="Not read since it changed. Edit it and Apply to see it." />;
  }
  const signal = signalOf(panel);
  if (reading.state === "ok" && signal) {
    return (
      <TimePanel
        reading={reading}
        signal={signal}
        zone={zone ?? Intl.DateTimeFormat().resolvedOptions().timeZone}
        description={`${panelName(panel)}, as it reads now.`}
      />
    );
  }
  return (
    <PanelWords
      words={
        stateWords(reading, panel, { canEdit: true, owner: null, projectName, overLimit: 0 }) ?? ""
      }
    />
  );
}

function tooManyReads(reads: number): string {
  return `This dashboard would need ${reads} reads, and one page may make ${MAX_SCANS_PER_READ}. Remove panels or use the picker.`;
}

/** What the page can say before anything is sent. The API's validator stays the judge. */
function checkDraft(document: DashboardDocument, reads: number): string[] {
  const found: string[] = [];
  if (document.title.trim() === "") found.push("Give the dashboard a title.");
  const followsPicker = document.definition.panels.some(
    (panel) => typeof panel.settings.service === "object" && panel.settings.service !== null,
  );
  if (followsPicker && !document.definition.service_picker) {
    found.push(
      "A panel follows the service picker, and the picker is off. Turn it on, or change that panel.",
    );
  }
  if (reads > MAX_SCANS_PER_READ) found.push(tooManyReads(reads));
  return found;
}

const FIELD_NAMES: readonly [RegExp, (match: RegExpExecArray) => string][] = [
  [/^body\.title$/, () => "Title"],
  [/^body\.description$/, () => "Description"],
  [/^body\.definition\.links\.(\d+)\.url$/, (match) => `Link ${Number(match[1]) + 1} address`],
  [/^body\.definition\.links\.(\d+)\.label$/, (match) => `Link ${Number(match[1]) + 1} label`],
  [/^body\.definition\.panels\.(\d+)/, (match) => `Panel ${Number(match[1]) + 1}`],
];

/** A field path from the API, in the words the screen uses for it. */
function fieldName(field: string): string {
  for (const [pattern, name] of FIELD_NAMES) {
    const match = pattern.exec(field);
    if (match) return name(match);
  }
  return "The dashboard";
}

/** What the API refused, one sentence each, never the submitted values. */
function refusals(error: unknown): string[] {
  if (!(error instanceof ApiError)) return ["Could not save this dashboard. Try again."];
  const errors = error.problem?.errors;
  if (Array.isArray(errors) && errors.length > 0) {
    return (errors as { field?: string; reason?: string }[]).map(
      (each) => `${fieldName(each.field ?? "")}: ${visibleEscapes(each.reason ?? "refused")}`,
    );
  }
  return [error.problem?.detail ?? "Could not save this dashboard. Try again."];
}

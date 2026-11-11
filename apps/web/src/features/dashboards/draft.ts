import type { DashboardDocument, Definition, PanelHeight, StoredPanel } from "./api";
import { panelTitle, TIME_KINDS } from "./panel-words";

/**
 * The builder's draft (F20, T3.2): a pure reducer over the dashboard document, so every
 * keyboard action rests on a value in and a value out, tested without a browser.
 *
 * Nothing here writes. A draft exists only in the page until Save (design, *The builder*),
 * and the API's validator stays the judge of what may be stored (FR7); the caps here say
 * so early, in the same words.
 */

/** `dashboards/definition.py`'s `MAX_PANELS`, held equal by `DashboardEditScreen.test.ts`. */
export const MAX_PANELS = 30;
/** `ratelimit/policy.py`'s `MAX_SCANS_PER_READ`, held equal the same way. */
export const MAX_SCANS_PER_READ = 40;
/** `dashboards/definition.py`'s `MAX_LINKS`. */
export const MAX_LINKS = 5;

/** A panel's share of a 12-column row (FR5). */
export const WIDTHS = [3, 4, 6, 8, 12] as const;
const HEIGHTS: readonly PanelHeight[] = ["small", "medium", "large"];
/** A chart that small has about 48 px of plot, so a time panel is never `small` (FR5). */
const TIME_HEIGHTS: readonly PanelHeight[] = ["medium", "large"];

const PANEL_ID_PREFIX = "p_";
/** The worst case counts each picker reference as one service apart from named ones. */
const PICKED_SERVICE = "\u0000picked";

export interface Draft {
  document: DashboardDocument;
  /** The panel last removed and where it stood, so Remove can be undone (FR21). */
  removed?: { panel: StoredPanel; index: number };
}

export type Step = -1 | 1;

export type DraftAction =
  | { type: "add"; panel: StoredPanel }
  | { type: "replace"; panel: StoredPanel }
  | { type: "move"; id: string; by: Step }
  | { type: "resize"; id: string; by: Step }
  | { type: "height"; id: string; by: Step }
  | { type: "duplicate"; id: string }
  | { type: "remove"; id: string }
  | { type: "undo-remove" }
  | { type: "set"; changes: Partial<Pick<DashboardDocument, "title" | "description">> }
  | {
      type: "settings";
      changes: Partial<Omit<Definition, "panels" | "schema_version">>;
    }
  | { type: "reset"; document: DashboardDocument };

export function blankDocument(): DashboardDocument {
  return {
    title: "",
    description: "",
    definition: {
      schema_version: 1,
      refresh_seconds: 60,
      default_range: null,
      service_picker: false,
      links: [],
      panels: [],
    },
  };
}

export function draftOf(document: DashboardDocument): Draft {
  return { document };
}

/** The smallest `p_N` not in use: short, unique, and safe in an address. */
export function newPanelId(panels: readonly StoredPanel[]): string {
  const taken = new Set(panels.map((panel) => panel.id));
  let number = 1;
  while (taken.has(`${PANEL_ID_PREFIX}${number}`)) number += 1;
  return `${PANEL_ID_PREFIX}${number}`;
}

export function heightsFor(panel: Pick<StoredPanel, "kind">): readonly PanelHeight[] {
  return TIME_KINDS.has(panel.kind) ? TIME_HEIGHTS : HEIGHTS;
}

export function canMove(panels: readonly StoredPanel[], id: string, by: Step): boolean {
  const index = panels.findIndex((panel) => panel.id === id);
  return index !== -1 && index + by >= 0 && index + by < panels.length;
}

export function canResize(panel: StoredPanel, by: Step): boolean {
  return stepped(WIDTHS, panel.width, by) !== undefined;
}

export function canChangeHeight(panel: StoredPanel, by: Step): boolean {
  return stepped(heightsFor(panel), panel.height, by) !== undefined;
}

/** The next value along `values` from `current`, or undefined past either end. */
function stepped<T>(values: readonly T[], current: T, by: Step): T | undefined {
  const index = values.indexOf(current);
  // A stored value off the list (an old width) steps to the nearest end.
  if (index === -1) return by > 0 ? values[values.length - 1] : values[0];
  return values[index + by];
}

export function reduceDraft(draft: Draft, action: DraftAction): Draft {
  const panels = draft.document.definition.panels;
  const withPanels = (next: StoredPanel[], removed?: Draft["removed"]): Draft => ({
    document: {
      ...draft.document,
      definition: { ...draft.document.definition, panels: next },
    },
    removed,
  });
  const index = "id" in action ? panels.findIndex((panel) => panel.id === action.id) : -1;
  const target = index === -1 ? undefined : panels[index];

  switch (action.type) {
    case "add":
      if (panels.length >= MAX_PANELS) return draft;
      return withPanels([...panels, action.panel]);
    case "replace": {
      const at = panels.findIndex((panel) => panel.id === action.panel.id);
      if (at === -1) return draft;
      return withPanels(panels.map((panel, position) => (position === at ? action.panel : panel)));
    }
    case "move": {
      if (!target || !canMove(panels, action.id, action.by)) return draft;
      const next = [...panels];
      next.splice(index, 1);
      next.splice(index + action.by, 0, target);
      return withPanels(next);
    }
    case "resize": {
      const width = target ? stepped(WIDTHS, target.width, action.by) : undefined;
      if (!target || width === undefined) return draft;
      return withPanels(replaced(panels, index, { ...target, width }));
    }
    case "height": {
      const height = target ? stepped(heightsFor(target), target.height, action.by) : undefined;
      if (!target || height === undefined) return draft;
      return withPanels(replaced(panels, index, { ...target, height }));
    }
    case "duplicate": {
      if (!target || panels.length >= MAX_PANELS) return draft;
      const copy = { ...target, id: newPanelId(panels) };
      return withPanels([...panels.slice(0, index + 1), copy, ...panels.slice(index + 1)]);
    }
    case "remove":
      if (!target) return draft;
      return withPanels(
        panels.filter((panel) => panel.id !== action.id),
        { panel: target, index },
      );
    case "undo-remove": {
      if (!draft.removed || panels.length >= MAX_PANELS) return draft;
      const { panel, index: at } = draft.removed;
      return withPanels([...panels.slice(0, at), panel, ...panels.slice(at)]);
    }
    case "set":
      return { document: { ...draft.document, ...action.changes } };
    case "settings":
      return {
        document: {
          ...draft.document,
          definition: { ...draft.document.definition, ...action.changes },
        },
      };
    case "reset":
      return draftOf(action.document);
  }
}

function replaced(panels: readonly StoredPanel[], index: number, panel: StoredPanel) {
  return panels.map((each, position) => (position === index ? panel : each));
}

/** One spelling of a value, keys sorted, so equal documents compare equal. */
function canonical(value: unknown): string {
  return JSON.stringify(value, (_, inner: unknown) =>
    inner !== null && typeof inner === "object" && !Array.isArray(inner)
      ? Object.fromEntries(
          Object.entries(inner as Record<string, unknown>).sort(([a], [b]) =>
            a < b ? -1 : a > b ? 1 : 0,
          ),
        )
      : inner,
  );
}

/**
 * Whether a value differs from what is stored (FR20, AC133). Never "was touched": a
 * change undone by hand is not a change, or the warning becomes noise.
 */
export function isDirty(document: DashboardDocument, stored: DashboardDocument): boolean {
  return canonical(document) !== canonical(stored);
}

/** The reads a definition needs at worst, as the API counts them at save (FR7). */
export function worstCaseReads(definition: Definition): number {
  const keys = new Set<string>();
  for (const panel of definition.panels) {
    if (panel.kind === "project_trend") keys.add("project");
    if (panel.kind === "service_trend") {
      const service = panel.settings.service;
      keys.add(`service:${typeof service === "string" ? service : PICKED_SERVICE}`);
    }
    // One read answers every signal of a ranking and count (`compile.py`).
    if (panel.kind === "top_services_trend") {
      keys.add(`top:${String(panel.settings.order)}:${String(panel.settings.count)}`);
    }
    // One read per node and metric.
    if (panel.kind === "node_trend") {
      keys.add(`node:${JSON.stringify(panel.settings.node)}:${String(panel.settings.metric)}`);
    }
  }
  return keys.size;
}

/** How a panel is named to a person: its service first, as its heading reads (FR35). */
export function panelName(panel: StoredPanel): string {
  const title = panelTitle(panel);
  return title.service ? `${title.service} · ${title.text}` : title.text;
}

/** What the live region says about one change (FR21, AC37). */
export function announce(action: DraftAction, before: Draft, after: Draft): string {
  const beforePanels = before.document.definition.panels;
  const afterPanels = after.document.definition.panels;
  const nameOf = (id: string) => {
    const panel =
      afterPanels.find((each) => each.id === id) ?? beforePanels.find((each) => each.id === id);
    return panel ? panelName(panel) : "Panel";
  };
  const position = (id: string) =>
    `position ${afterPanels.findIndex((panel) => panel.id === id) + 1} of ${afterPanels.length}`;

  switch (action.type) {
    case "move":
      if (after === before) {
        return `${nameOf(action.id)} is already ${action.by < 0 ? "first" : "last"}`;
      }
      return `${nameOf(action.id)} moved to ${position(action.id)}`;
    case "resize": {
      const panel = afterPanels.find((each) => each.id === action.id);
      return `${nameOf(action.id)} is ${after === before ? "already" : "now"} ${panel?.width ?? ""} wide`;
    }
    case "height": {
      const panel = afterPanels.find((each) => each.id === action.id);
      return `${nameOf(action.id)} is ${after === before ? "already" : "now"} ${panel?.height ?? ""}`;
    }
    case "add": {
      if (after === before) return `A dashboard holds ${MAX_PANELS} panels`;
      return `${nameOf(action.panel.id)} added at ${position(action.panel.id)}`;
    }
    case "duplicate": {
      if (after === before) return `A dashboard holds ${MAX_PANELS} panels`;
      const copy = afterPanels.find((panel) => !beforePanels.some((each) => each.id === panel.id));
      return copy
        ? `${nameOf(action.id)} duplicated at ${position(copy.id)}`
        : `${nameOf(action.id)} duplicated`;
    }
    case "remove":
      return `${nameOf(action.id)} removed. Undo available`;
    case "undo-remove": {
      const restored = before.removed?.panel;
      if (!restored || after === before) return "Nothing to undo";
      return `${panelName(restored)} restored at ${position(restored.id)}`;
    }
    case "replace":
      return `${panelName(action.panel)} changed`;
    case "set":
    case "settings":
    case "reset":
      return "";
  }
}

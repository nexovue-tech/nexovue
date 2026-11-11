import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";

import type { PanelResult, Signal, StoredPanel } from "./api";

/**
 * What a panel is called and what it says when it has no line to draw (F20, design
 * *Words by kind*). Pure, so each sentence is tested without a canvas.
 *
 * Every telemetry value and stored text goes through `visibleEscapes` (FR61). Words that
 * name an action follow the reader's role: a viewer is never told to remove panels (FR65).
 */

export const TIME_KINDS = new Set([
  "project_trend",
  "service_trend",
  "top_services_trend",
  "node_trend",
]);

/** A node as the infrastructure screen names it: its container, its host, or its reporter. */
export interface NodeRef {
  host: string;
  container: string;
  reporter: string;
  reporter_instance: string;
}

export function nodeOf(panel: StoredPanel): NodeRef | undefined {
  const node = panel.settings.node;
  if (typeof node !== "object" || node === null) return undefined;
  const { host, container, reporter, reporter_instance } = node as Record<string, unknown>;
  return typeof host === "string" &&
    typeof container === "string" &&
    typeof reporter === "string" &&
    typeof reporter_instance === "string"
    ? { host, container, reporter, reporter_instance }
    : undefined;
}

/** "checkout" for a container, "web-01" for a host, "reported by recommendation" otherwise. */
export function nodeName(node: NodeRef): string {
  if (node.container) return visibleEscapes(node.container);
  if (node.host) return visibleEscapes(node.host);
  return `reported by ${visibleEscapes(node.reporter)}`;
}

const SIGNAL_NAMES: Record<Signal, string> = {
  rate: "Rate",
  errors: "Errors",
  latency: "Latency",
};

export function signalOf(panel: StoredPanel): Signal | undefined {
  const signal = panel.settings.signal;
  return signal === "rate" || signal === "errors" || signal === "latency" ? signal : undefined;
}

/** The named service, or undefined for the picker or a panel with none. */
export function serviceOf(panel: StoredPanel): string | undefined {
  const service = panel.settings.service;
  return typeof service === "string" ? service : undefined;
}

/** A generated title always says what is shown, with the service first (FR35). */
export function panelTitle(panel: StoredPanel): { service?: string; text: string } {
  const signal = signalOf(panel);
  const what = panel.title ? visibleEscapes(panel.title) : signal ? SIGNAL_NAMES[signal] : "Note";
  if (panel.kind === "service_trend") {
    const service = serviceOf(panel);
    return {
      service: service === undefined ? "Picked service" : visibleEscapes(service),
      text: what,
    };
  }
  if (panel.kind === "top_services_trend") {
    const count = Number(panel.settings.count) || 0;
    const order = panel.settings.order === "slowest" ? "Slowest" : "Worst";
    return {
      text: panel.title
        ? visibleEscapes(panel.title)
        : `${order} ${count} ${count === 1 ? "service" : "services"} · ${what}`,
    };
  }
  if (panel.kind === "node_trend") {
    const node = nodeOf(panel);
    const metric = typeof panel.settings.metric === "string" ? panel.settings.metric : "";
    return {
      text: panel.title
        ? visibleEscapes(panel.title)
        : `${node ? nodeName(node) : "A machine"} · ${visibleEscapes(metric)}`,
    };
  }
  if (!TIME_KINDS.has(panel.kind) && panel.kind !== "note") {
    return { text: panel.title ? visibleEscapes(panel.title) : "Panel" };
  }
  return { text: what };
}

export interface WordsContext {
  canEdit: boolean;
  owner: string | null;
  projectName: string;
  /** Panels past the scan cap, for the editor's "Remove N panels". */
  overLimit: number;
  now?: number;
}

/** The sentence a panel shows in place of a line. Undefined when it draws one. */
export function stateWords(
  result: PanelResult,
  panel: StoredPanel,
  context: WordsContext,
): string | undefined {
  switch (result.state) {
    case "ok":
      return undefined;
    case "empty":
      if (panel.kind === "top_services_trend") return "No services reported in this window.";
      if (panel.kind === "node_trend") {
        const node = nodeOf(panel);
        return `${node ? nodeName(node) : "This machine"} has not reported in this window.`;
      }
      if (panel.kind !== "service_trend") return "Nothing reported in this window.";
      return result.last_seen_at === null
        ? "No requests in this window."
        : `No requests in this window. Last seen ${formatRelativeTime(result.last_seen_at, context.now)}.`;
    case "not_in_project":
      return `No record of ${serviceOf(panel) ? visibleEscapes(serviceOf(panel) ?? "") : "this service"} in ${visibleEscapes(context.projectName)}.`;
    case "needs_service":
      return "Choose a service to fill this dashboard.";
    case "over_limit":
      return context.canEdit
        ? `This dashboard asks for more reads than one page may make. Remove ${context.overLimit} ${context.overLimit === 1 ? "panel" : "panels"}.`
        : `This dashboard asks for more reads than one page may make. Ask ${context.owner ? visibleEscapes(context.owner) : "the person who made it"}, or someone who can edit it, to remove panels.`;
    case "refused":
      return "Too many reads just now.";
    case "timed_out":
      return "The database took too long to answer.";
    case "failed":
      return "Could not read this.";
    case "unknown_kind":
      return "This panel type is no longer available.";
  }
}

/** Panels that hold no reading of this answer: the header's "could not be read" count (FR64). */
export function unread(result: PanelResult): boolean {
  return (
    result.state === "failed" ||
    result.state === "timed_out" ||
    result.state === "refused" ||
    result.state === "over_limit" ||
    result.state === "unknown_kind"
  );
}

/** A read that failed for now and may succeed on the next, so an earlier line may stay. */
export function transient(result: PanelResult): boolean {
  return result.state === "failed" || result.state === "timed_out" || result.state === "refused";
}

import { formatDate } from "./format/time";
import { urlWindowSearch } from "./url-window";

/**
 * Every link a finding's explanation offers, built here and nowhere else (F3 FR16, FR17,
 * FR42, FR53; design, *Links, and the window in the URL*). Pure, so each shape is tested.
 * In `shared/` because two features use it: the finding page, and an incident's page
 * with no finding (FR53), and features never import each other.
 *
 * **A link carries the finding's window in the address** (`from`, `to`, `project`,
 * `finding`), so it opens the right minutes, a copied link opens them again, and the
 * person's own window on every other screen is left as it was (`shared/url-window.ts`).
 *
 * **A link opens the data, not just the service.** An error fact opens its group; a
 * dependency fact opens traces filtered to the callee and to failing or slow calls; a
 * slower finding's traces open at its worst reading. A link to raw data past its
 * retention is a sentence saying so instead (FR17).
 */

/** What a fact's link needs, as the explanation API gives it (`findings/schemas.py`). */
export interface ExplanationFactTarget {
  kind: "error_group" | "dependency" | "deploy" | "host" | "finding" | "check" | "incident";
  group_id?: string | null;
  services?: string[];
  caller?: string | null;
  callee?: string | null;
  callee_attr?: string | null;
  failure_share_before?: number | null;
  failure_share_during?: number | null;
  p95_before?: number | null;
  p95_during?: number | null;
  release_id?: string | null;
  id?: string | null;
}

/** The part of a finding's signal the traces link reads: a `slower` one's worst reading. */
export interface SignalReading {
  signal: string;
  worst: number | null;
  unit: string;
}

/** A finding's window: its start less this, to its end plus the other, or now (guesses). */
export const WINDOW_LEAD_MS = 15 * 60_000;
export const WINDOW_TRAIL_MS = 5 * 60_000;

export type LinkTarget =
  | "/traces"
  | "/logs"
  | "/errors"
  | "/releases"
  | "/releases/$releaseId"
  | "/infrastructure"
  | "/findings/$findingId"
  | "/synthetics/$checkId"
  | "/alerts/incidents/$incidentId"
  | "/services/$name";

export interface EvidenceLink {
  kind: "link";
  label: string;
  to: LinkTarget;
  params?: Record<string, string>;
  search: Record<string, string>;
}

/** FR17: what a link to aged-out raw data reads instead of opening an empty screen. */
export interface AgedOut {
  kind: "aged_out";
  label: string;
}

export type LookNextEntry = EvidenceLink | AgedOut;

/** What a window and its links are built from: a finding, or an incident (FR53). */
export interface WindowSource {
  project_id: string;
  started_at: string;
  closed_at: string | null;
  /** Until when the window's raw data is kept; null or absent when not known. */
  raw_kept_until?: string | null;
  /** The finding, when the links come from one, for "Back to the finding". */
  finding_id?: string;
}

/** The finding's minutes, as the address carries them (design, *Links*). */
export function findingWindow(
  source: Pick<WindowSource, "started_at" | "closed_at">,
  now = Date.now(),
): { from: string; to: string } {
  const started = Date.parse(source.started_at);
  const ended = source.closed_at === null ? now : Date.parse(source.closed_at) + WINDOW_TRAIL_MS;
  return {
    from: new Date(started - WINDOW_LEAD_MS).toISOString(),
    to: new Date(Math.min(ended, now)).toISOString(),
  };
}

function windowSearch(source: WindowSource, now: number): Record<string, string> {
  return urlWindowSearch({
    ...findingWindow(source, now),
    project: source.project_id,
    finding: source.finding_id,
  });
}

/** Whether the raw data behind this window has aged out (FR17). */
export function rawAgedOut(source: WindowSource, now = Date.now()): boolean {
  if (!source.raw_kept_until) return false;
  return Date.parse(source.raw_kept_until) < now;
}

function agedOut(what: "traces" | "logs" | "errors", source: WindowSource): AgedOut {
  return {
    kind: "aged_out",
    label: `Raw ${what} aged out on ${formatDate(source.raw_kept_until ?? "")}`,
  };
}

function raw(
  what: "traces" | "logs" | "errors",
  source: WindowSource,
  now: number,
  link: Omit<EvidenceLink, "kind" | "search"> & { search?: Record<string, string> },
): LookNextEntry {
  if (rawAgedOut(source, now)) return agedOut(what, source);
  return {
    kind: "link",
    ...link,
    search: { ...windowSearch(source, now), ...(link.search ?? {}) },
  };
}

function windowed(
  source: WindowSource,
  now: number,
  link: Omit<EvidenceLink, "kind" | "search"> & { search?: Record<string, string> },
): EvidenceLink {
  return {
    kind: "link",
    ...link,
    search: { ...windowSearch(source, now), ...(link.search ?? {}) },
  };
}

/** The slowest traces a `slower` finding has: at or over its worst reading (FR16). */
function slowestFrom(signals: readonly SignalReading[]): Record<string, string> {
  const slower = signals.find((signal) => signal.signal === "slower");
  if (slower?.worst == null || slower.unit !== "ms" || slower.worst <= 0) return {};
  return { min_duration_ms: String(Math.floor(slower.worst)) };
}

/**
 * FR42, FR53: "Look next", from the finding (or incident) itself, so it renders in every
 * state of the explanation, while it loads and when its request fails (AC66, AC101).
 */
export function lookNext(
  source: WindowSource & {
    service_name: string | null;
    signals?: readonly SignalReading[];
  },
  now = Date.now(),
): LookNextEntry[] {
  const service = source.service_name;
  const scoped = (key: string): Record<string, string> => (service ? { [key]: service } : {});
  return [
    raw("traces", source, now, {
      label: "Traces",
      to: "/traces",
      search: { ...scoped("service"), ...slowestFrom(source.signals ?? []) },
    }),
    raw("logs", source, now, { label: "Logs", to: "/logs", search: scoped("service") }),
    raw("errors", source, now, { label: "Errors", to: "/errors", search: scoped("service_name") }),
    windowed(source, now, { label: "Deploys", to: "/releases" }),
    windowed(source, now, { label: "Infrastructure (whole project)", to: "/infrastructure" }),
  ];
}

/** A told-only callee's name is in parentheses (`serviceedges`, A76). */
function told(callee: string): string | null {
  return callee.startsWith("(") && callee.endsWith(")") ? callee.slice(1, -1) : null;
}

/**
 * What a dependency fact's traces are filtered to (A76, AC105), as the address carries
 * them. A callee that reports for itself is `service`; one only its caller named is the
 * caller's calls with the attribute that named it; with no such attribute, the caller's
 * calls, labelled as not narrowed. Each adds failing calls, or slow ones for a latency
 * move.
 */
export function dependencySearch(target: ExplanationFactTarget): {
  label: string;
  search: Record<string, string>;
} {
  const callee = target.callee ?? "";
  const caller = target.caller ?? null;
  const named = told(callee);
  let label: string;
  const search: Record<string, string> = {};
  if (named === null) {
    label = `Traces through ${callee}`;
    search.service = callee;
  } else if (target.callee_attr && caller) {
    label = `Calls from ${caller} to ${named}`;
    search.service = caller;
    search.attr = target.callee_attr;
    search.attr_value = named;
  } else {
    label = caller
      ? `Calls from ${caller}, not narrowed to ${named}`
      : `Traces, not narrowed to ${named}`;
    if (caller) search.service = caller;
  }
  const before = target.failure_share_before ?? null;
  const during = target.failure_share_during ?? null;
  const slowBefore = target.p95_before ?? null;
  const slowDuring = target.p95_during ?? null;
  if (during !== null && during > (before ?? 0)) {
    search.status = "ERROR";
  } else if (slowBefore !== null && slowDuring !== null && slowDuring > slowBefore) {
    search.min_duration_ms = String(Math.floor(slowBefore));
  }
  return { label, search };
}

/** A fact's own link, by kind (design, *Links*), or null for a kind with none. */
export function factLink(
  fact: { target?: ExplanationFactTarget | null },
  source: WindowSource,
  now = Date.now(),
): LookNextEntry | null {
  const target = fact.target ?? null;
  if (target === null) return null;
  switch (target.kind) {
    case "error_group": {
      if (!target.group_id) return null;
      const service = target.services?.[0];
      return raw("errors", source, now, {
        label: "Errors",
        to: "/errors",
        search: { group: target.group_id, ...(service ? { service_name: service } : {}) },
      });
    }
    case "dependency": {
      if (!target.callee) return null;
      const { label, search } = dependencySearch(target);
      return raw("traces", source, now, { label, to: "/traces", search });
    }
    case "deploy":
      return target.release_id
        ? {
            kind: "link",
            label: "Release",
            to: "/releases/$releaseId",
            params: { releaseId: target.release_id },
            search: {},
          }
        : windowed(source, now, { label: "Deploys", to: "/releases" });
    case "host":
      return windowed(source, now, {
        label: "Infrastructure (whole project)",
        to: "/infrastructure",
      });
    case "finding":
      return target.id
        ? {
            kind: "link",
            label: "Its finding",
            to: "/findings/$findingId",
            params: { findingId: target.id },
            search: {},
          }
        : null;
    case "check":
      return target.id
        ? {
            kind: "link",
            label: "The check",
            to: "/synthetics/$checkId",
            params: { checkId: target.id },
            search: {},
          }
        : null;
    case "incident":
      return target.id
        ? {
            kind: "link",
            label: "The alert",
            to: "/alerts/incidents/$incidentId",
            params: { incidentId: target.id },
            search: {},
          }
        : null;
    default:
      return null;
  }
}

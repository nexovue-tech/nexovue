import { ApiError } from "@/shared/api-client";

import type {
  AlertableState,
  AlertRule,
  ChannelKind,
  ChannelSummary,
  Condition,
  ConditionKind,
  Incident,
  IncidentNotification,
  OpenIncidentCount,
  Severity,
  WriteRule,
} from "./api";

/**
 * The words the alert screens use, and the drafts their forms edit. Pure, so every
 * sentence a person reads is tested without rendering anything.
 */

export const KIND_NAMES: Record<ChannelKind, string> = {
  owners: "Organization owners",
  email: "Email",
  slack: "Slack",
  teams: "Microsoft Teams",
  discord: "Discord",
  pagerduty: "PagerDuty",
  webhook: "Webhook",
};

export const STATE_WORDS: Record<AlertableState, string> = {
  down: "down",
  degraded: "degraded",
  misconfigured: "misconfigured",
};

export const ALERTABLE_STATES: readonly AlertableState[] = ["down", "degraded", "misconfigured"];

/** The spec's defaults (FR5): down is critical, the other two are warnings. */
export const DEFAULT_SEVERITY: Record<AlertableState, Severity> = {
  down: "critical",
  degraded: "warning",
  misconfigured: "warning",
};

export interface CheckChoice {
  id: string;
  name: string;
}

export const CONDITION_NAMES: Record<ConditionKind, string> = {
  check_state: "A check fails",
  service_error_rate: "A service's error rate",
  service_latency_p95: "A service's p95 latency",
  service_silent: "A service goes silent",
  new_error_group: "A new error appears",
  anomaly_finding: "Anomaly detection finds a problem",
};

/** The kinds that decide whether detection is heard: an owner's or an admin's (D11). */
export const DETECTION_KINDS: readonly ConditionKind[] = ["anomaly_finding"];

/** A rule's condition as a sentence: *"Any check is down or degraded"*. */
export function describeCondition(
  rule: Pick<AlertRule, "condition">,
  checks?: readonly CheckChoice[],
): string {
  const condition = rule.condition;
  switch (condition.kind) {
    case "check_state": {
      const states = ALERTABLE_STATES.filter((state) => condition.states[state] !== undefined).map(
        (state) => STATE_WORDS[state],
      );
      return `${describeChecks(condition.check_ids, checks)} is ${joinOr(states)}`;
    }
    case "service_error_rate":
      return `${describeServices(condition.services, "error rate")} at or over ${condition.threshold_percent}% for ${condition.fire_after_minutes} min`;
    case "service_latency_p95":
      return `${describeServices(condition.services, "p95")} at or over ${condition.threshold_ms} ms for ${condition.fire_after_minutes} min`;
    case "service_silent":
      return `${describeServices(condition.services)} sends nothing for ${condition.silent_minutes} min`;
    case "new_error_group":
      return `A new error in ${condition.services === null ? "any service" : joinOr(condition.services)}`;
    case "anomaly_finding":
      return `Anomaly detection finds a ${condition.minimum_severity === "critical" ? "critical problem" : "problem"} in ${condition.services === null ? "any service" : joinOr(condition.services)}`;
  }
}

function describeServices(services: string[] | null, reading?: string): string {
  const who = services === null ? "Any service" : joinOr(services);
  if (reading === undefined) return who;
  return services === null ? `${who}'s ${reading}` : `${who} ${reading}`;
}

function describeChecks(checkIds: string[] | null, checks?: readonly CheckChoice[]): string {
  if (checkIds === null) return "Any check";
  if (checkIds.length === 1) {
    const found = checks?.find((check) => check.id === checkIds[0]);
    return found ? found.name : "One chosen check";
  }
  return `Any of ${checkIds.length} chosen checks`;
}

function joinOr(words: string[]): string {
  if (words.length <= 1) return words[0] ?? "";
  return `${words.slice(0, -1).join(", ")} or ${words[words.length - 1]}`;
}

/**
 * What a rule is doing, in the order that matters most. A rule that notifies nobody or
 * covers nothing is broken, and is said so before anything else (FR25).
 */
export function ruleStatus(
  rule: AlertRule,
  now: Date = new Date(),
): { label: string; tone: "critical" | "warning" | "none" } {
  if (rule.notifying_nobody) return { label: "Not notifying anyone", tone: "critical" };
  if (rule.covers_nothing) return { label: "Covers nothing", tone: "warning" };
  if (!rule.enabled) return { label: "Off", tone: "none" };
  if (isMuted(rule.muted_until, now)) {
    return { label: `Muted until ${formatUntil(rule.muted_until)}`, tone: "warning" };
  }
  return { label: "Active", tone: "none" };
}

export function isMuted(mutedUntil: string | null, now: Date = new Date()): boolean {
  return mutedUntil !== null && new Date(mutedUntil).getTime() > now.getTime();
}

const UNTIL = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatUntil(at: string | null): string {
  if (at === null) return "—";
  const moment = new Date(at);
  return Number.isNaN(moment.getTime()) ? "—" : UNTIL.format(moment);
}

/** A mute's end, for the choices FR14 names: an hour, four, or tomorrow morning. */
export function muteUntil(choice: "1h" | "4h" | "tomorrow", now: Date = new Date()): Date {
  if (choice === "1h") return new Date(now.getTime() + 60 * 60_000);
  if (choice === "4h") return new Date(now.getTime() + 4 * 60 * 60_000);
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(9, 0, 0, 0);
  return tomorrow;
}

/** *4 min*, *2 h 5 min*. Coarse on purpose, as the messages are. */
export function describeDuration(
  fromIso: string,
  toIso: string | null,
  now: Date = new Date(),
): string {
  const end = toIso === null ? now : new Date(toIso);
  const minutes = Math.max(1, Math.round((end.getTime() - new Date(fromIso).getTime()) / 60_000));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours && rest) return `${hours} h ${rest} min`;
  return hours ? `${hours} h` : `${rest} min`;
}

/**
 * What the incident is, in the words it opened with — "is down", "error rate 12.3%,
 * over 5%" — whether or not it has since resolved.
 */
export function describeHappening(incident: Incident): string {
  if (incident.condition_kind === "new_error_group") return "is a new error";
  const detail = incident.detail;
  if (detail.reading === "silence") return "went silent";
  if (detail.reading && typeof detail.value === "number") {
    const value =
      detail.unit === "%" ? `${detail.value.toFixed(1)}%` : `${Math.round(detail.value)} ms`;
    return `${detail.reading} ${value}, over ${detail.threshold}${detail.unit === "%" ? "%" : " ms"}`;
  }
  return detail.state ? `is ${detail.state}` : "fired";
}

/** Where an incident stands, in one line: open and for how long, or when it ended. */
export function describeIncidentState(incident: Incident, now: Date = new Date()): string {
  if (incident.is_event) return "Said once — a new error has nothing to resolve.";
  if (incident.resolved_at === null) {
    return `Open for ${describeDuration(incident.opened_at, null, now)}.`;
  }
  return `Resolved after ${describeDuration(incident.opened_at, incident.resolved_at)}.`;
}

const NOTIFICATION_KIND_WORDS: Record<IncidentNotification["kind"], string> = {
  opened: "Opened",
  escalated: "Got worse",
  resolved: "Resolved",
  repeated: "Reminder",
  flapping: "Flapping",
  closed: "Closed",
};

/** One line of "who was told": what was said, to whom, and whether it got there. */
export function describeNotification(notification: IncidentNotification): string {
  const what = `${NOTIFICATION_KIND_WORDS[notification.kind]} → ${notification.channel_name}`;
  switch (notification.state) {
    case "delivered":
      return `${what}: delivered`;
    case "failed":
      return `${what}: failed${notification.reason ? ` — ${notification.reason}` : ""}`;
    case "dropped":
      return `${what}: not sent${notification.reason ? ` — ${notification.reason}` : ""}`;
    default:
      return `${what}: sending`;
  }
}

/** Past this the badge stops counting; the Alerts screen has the rest. */
const BADGE_MOST = 99;

/**
 * The badge beside Alerts in the sidebar, or null when nothing is open — a "0"
 * would be one more thing on every screen to read and dismiss.
 */
export function openIncidentBadge(
  count: OpenIncidentCount,
): { text: string; label: string; severity: Severity } | null {
  if (count.open === 0) return null;
  return {
    text: count.open > BADGE_MOST ? `${BADGE_MOST}+` : String(count.open),
    label: count.open === 1 ? "1 open incident" : `${count.open} open incidents`,
    severity: count.critical > 0 ? "critical" : "warning",
  };
}

/** Who an incident's opening was sent to, and whether it got there. */
export function describeTold(incident: Incident): string {
  const opened = incident.notifications.filter((notification) => notification.kind === "opened");
  if (incident.muted) return "Muted — nobody was told";
  if (opened.length === 0) return "Nobody was told";
  return opened
    .map((notification) => {
      const state =
        notification.state === "delivered"
          ? ""
          : notification.state === "failed"
            ? " (failed)"
            : notification.state === "dropped"
              ? " (muted)"
              : " (sending)";
      return `${notification.channel_name}${state}`;
    })
    .join(", ");
}

/* --------------------------------------------------------------- rule draft */

export interface RuleDraft {
  kind: ConditionKind;
  name: string;
  /** Every check or service, or the chosen ones. */
  scope: "all" | "chosen";
  checkIds: string[];
  services: string[];
  states: Partial<Record<AlertableState, Severity>>;
  severity: Severity;
  /** Percent for an error rate, milliseconds for p95. Text while it is typed. */
  threshold: string;
  windowMinutes: string;
  minRequests: string;
  fireAfter: string;
  resolveAfter: string;
  silentMinutes: string;
  channelIds: string[];
  /** Minutes, or "" for off. */
  renotify: string;
  enabled: boolean;
}

export type RuleField =
  | "name"
  | "checks"
  | "services"
  | "states"
  | "threshold"
  | "windowMinutes"
  | "minRequests"
  | "waits"
  | "silentMinutes"
  | "channels";

/** The spec's defaults for each kind (FR5). */
export const KIND_DEFAULTS: Record<
  Exclude<ConditionKind, "check_state">,
  { severity: Severity; threshold: string }
> = {
  service_error_rate: { severity: "critical", threshold: "5" },
  service_latency_p95: { severity: "warning", threshold: "1000" },
  service_silent: { severity: "critical", threshold: "" },
  new_error_group: { severity: "warning", threshold: "" },
  // The default rule sends critical findings only (FR29); a new rule starts wider.
  anomaly_finding: { severity: "warning", threshold: "" },
};

export function emptyRuleDraft(
  from: { checkId?: string; checkName?: string; service?: string } = {},
): RuleDraft {
  const base: RuleDraft = {
    kind: "check_state",
    name: "",
    scope: "all",
    checkIds: [],
    services: [],
    states: { down: DEFAULT_SEVERITY.down },
    severity: "critical",
    threshold: "",
    windowMinutes: "5",
    minRequests: "20",
    fireAfter: "5",
    resolveAfter: "5",
    silentMinutes: "10",
    channelIds: [],
    renotify: "",
    enabled: true,
  };
  if (from.checkId) {
    return {
      ...base,
      name: from.checkName ? `${from.checkName} is down` : "",
      scope: "chosen",
      checkIds: [from.checkId],
    };
  }
  if (from.service) {
    return withKind(
      { ...base, name: `${from.service} error rate`, scope: "chosen", services: [from.service] },
      "service_error_rate",
    );
  }
  return base;
}

/** The draft with another kind of condition, and that kind's defaults. */
export function withKind(draft: RuleDraft, kind: ConditionKind): RuleDraft {
  if (kind === "check_state") return { ...draft, kind, severity: "critical" };
  const defaults = KIND_DEFAULTS[kind];
  return { ...draft, kind, severity: defaults.severity, threshold: defaults.threshold };
}

export function ruleDraftOf(rule: AlertRule): RuleDraft {
  const draft: RuleDraft = {
    ...emptyRuleDraft(),
    kind: rule.condition.kind,
    name: rule.name,
    channelIds: rule.channels.map((channel) => channel.id),
    renotify: rule.renotify_minutes === null ? "" : String(rule.renotify_minutes),
    enabled: rule.enabled,
  };
  const condition = rule.condition;
  if (condition.kind === "check_state") {
    return {
      ...draft,
      scope: condition.check_ids === null ? "all" : "chosen",
      checkIds: condition.check_ids ?? [],
      states: { ...condition.states },
    };
  }
  const services = {
    scope: condition.services === null ? ("all" as const) : ("chosen" as const),
    services: condition.services ?? [],
    // An anomaly rule's is a minimum, not the severity it gives.
    severity:
      condition.kind === "anomaly_finding" ? condition.minimum_severity : condition.severity,
  };
  switch (condition.kind) {
    case "service_error_rate":
    case "service_latency_p95":
      return {
        ...draft,
        ...services,
        threshold: String(
          condition.kind === "service_error_rate"
            ? condition.threshold_percent
            : condition.threshold_ms,
        ),
        windowMinutes: String(condition.window_minutes),
        minRequests: String(condition.min_requests),
        fireAfter: String(condition.fire_after_minutes),
        resolveAfter: String(condition.resolve_after_minutes),
      };
    case "service_silent":
      return { ...draft, ...services, silentMinutes: String(condition.silent_minutes) };
    case "new_error_group":
      return { ...draft, ...services };
    case "anomaly_finding":
      return { ...draft, ...services };
  }
}

const isWhole = (text: string, low: number, high: number) =>
  /^\d+$/.test(text.trim()) && Number(text) >= low && Number(text) <= high;

export function ruleProblems(draft: RuleDraft): Partial<Record<RuleField, string>> {
  const problems: Partial<Record<RuleField, string>> = {};
  if (!draft.name.trim()) problems.name = "Name the rule, so a message says which one fired.";
  if (draft.channelIds.length === 0) {
    problems.channels = "Choose where to send it. A rule with no channel tells nobody.";
  }
  if (draft.kind === "check_state") {
    if (draft.scope === "chosen" && draft.checkIds.length === 0) {
      problems.checks = "Choose at least one check, or cover every check.";
    }
    if (Object.keys(draft.states).length === 0) {
      problems.states = "Choose at least one state to alert on.";
    }
    return problems;
  }
  if (draft.scope === "chosen" && draft.services.length === 0) {
    problems.services = "Choose at least one service, or cover every service.";
  }
  if (draft.kind === "service_error_rate" || draft.kind === "service_latency_p95") {
    const limit = draft.kind === "service_error_rate" ? 100 : 600_000;
    const value = Number(draft.threshold);
    if (!draft.threshold.trim() || !Number.isFinite(value) || value <= 0 || value > limit) {
      problems.threshold =
        draft.kind === "service_error_rate"
          ? "A percentage above 0 and at most 100."
          : "Milliseconds above 0.";
    }
    if (!isWhole(draft.windowMinutes, 1, 60)) problems.windowMinutes = "1 to 60 minutes.";
    if (!isWhole(draft.minRequests, 1, 1_000_000)) problems.minRequests = "At least 1.";
    if (!isWhole(draft.fireAfter, 0, 120) || !isWhole(draft.resolveAfter, 0, 120)) {
      problems.waits = "0 to 120 minutes each.";
    }
  }
  if (draft.kind === "service_silent" && !isWhole(draft.silentMinutes, 5, 1440)) {
    problems.silentMinutes = "5 minutes to a day.";
  }
  return problems;
}

function conditionOf(draft: RuleDraft): Condition {
  if (draft.kind === "check_state") {
    return {
      kind: "check_state",
      check_ids: draft.scope === "all" ? null : draft.checkIds,
      states: draft.states,
    };
  }
  const scope = {
    services: draft.scope === "all" ? null : draft.services,
    severity: draft.severity,
  };
  const threshold = {
    ...scope,
    window_minutes: Number(draft.windowMinutes),
    min_requests: Number(draft.minRequests),
    fire_after_minutes: Number(draft.fireAfter),
    resolve_after_minutes: Number(draft.resolveAfter),
  };
  switch (draft.kind) {
    case "service_error_rate":
      return { kind: draft.kind, ...threshold, threshold_percent: Number(draft.threshold) };
    case "service_latency_p95":
      return { kind: draft.kind, ...threshold, threshold_ms: Number(draft.threshold) };
    case "service_silent":
      return { kind: draft.kind, ...scope, silent_minutes: Number(draft.silentMinutes) };
    case "new_error_group":
      return { kind: draft.kind, ...scope };
    case "anomaly_finding":
      // The draft's severity is the rule's minimum: a finding's own comes from the detector.
      return { kind: draft.kind, services: scope.services, minimum_severity: draft.severity };
  }
}

export function ruleRequestOf(draft: RuleDraft, projectId: string): WriteRule {
  return {
    project_id: projectId,
    name: draft.name.trim(),
    condition: conditionOf(draft),
    channel_ids: draft.channelIds,
    // Findings are never repeated (design, *What a finding sends*).
    renotify_minutes:
      draft.renotify === "" || draft.kind === "anomaly_finding" ? null : Number(draft.renotify),
    enabled: draft.enabled,
  };
}

/** Whether the draft still says what the form opened with — the stored rule, or the
 *  prefilled new one, so arriving from a check's page is not already "unsaved". */
export function sameRule(draft: RuleDraft, baseline: RuleDraft): boolean {
  return JSON.stringify(normalised(draft)) === JSON.stringify(normalised(baseline));
}

function normalised(draft: RuleDraft): RuleDraft {
  return {
    ...draft,
    checkIds: [...draft.checkIds].sort(),
    services: [...draft.services].sort(),
    channelIds: [...draft.channelIds].sort(),
    states: Object.fromEntries(Object.entries(draft.states).sort()),
  };
}

/* ------------------------------------------------------------ channel draft */

export type ChannelField = "name" | "secret" | "addresses" | "url" | "region";

export interface ChannelDraft {
  kind: Exclude<ChannelKind, "owners">;
  name: string;
  secret: string;
  addresses: string;
  url: string;
  region: "us" | "eu";
}

export const CREATABLE_KINDS: readonly Exclude<ChannelKind, "owners">[] = [
  "slack",
  "teams",
  "discord",
  "pagerduty",
  "webhook",
  "email",
];

/** What each kind asks for, in the words its own product uses. */
export const SECRET_LABEL: Partial<Record<ChannelKind, string>> = {
  slack: "Incoming webhook URL",
  teams: "Workflows webhook URL",
  discord: "Webhook URL",
  pagerduty: "Integration key (Events API v2)",
};

export function emptyChannelDraft(kind: ChannelDraft["kind"] = "slack"): ChannelDraft {
  return { kind, name: "", secret: "", addresses: "", url: "", region: "us" };
}

export function channelProblems(
  draft: ChannelDraft,
  { replacing = false }: { replacing?: boolean } = {},
): Partial<Record<ChannelField, string>> {
  const problems: Partial<Record<ChannelField, string>> = {};
  if (!draft.name.trim()) problems.name = "Name the channel, as it will appear on a rule.";
  if (SECRET_LABEL[draft.kind] !== undefined && !replacing && !draft.secret.trim()) {
    problems.secret = `Paste the ${SECRET_LABEL[draft.kind]?.toLowerCase()}.`;
  }
  if (
    SECRET_LABEL[draft.kind] !== undefined &&
    draft.kind !== "pagerduty" &&
    draft.secret.trim() &&
    !draft.secret.trim().startsWith("https://")
  ) {
    problems.secret = "It must be an https:// URL.";
  }
  if (draft.kind === "email" && addressesOf(draft.addresses).length === 0) {
    problems.addresses = "Add at least one address.";
  }
  if (draft.kind === "webhook" && !draft.url.trim().startsWith("https://")) {
    problems.url = "It must be an https:// URL.";
  }
  return problems;
}

export function addressesOf(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((address) => address.trim())
    .filter(Boolean);
}

export function channelConfigOf(draft: ChannelDraft): Record<string, unknown> {
  if (draft.kind === "email") return { addresses: addressesOf(draft.addresses) };
  if (draft.kind === "webhook") return { url: draft.url.trim() };
  if (draft.kind === "pagerduty") return { region: draft.region };
  return {};
}

/** Where a channel sends, as far as may be shown (no path, no key). */
export function describeDestination(channel: ChannelSummary): string {
  if (channel.kind === "owners") return "Every owner's verified address";
  if (channel.kind === "email") return (channel.config.addresses ?? []).join(", ");
  // A webhook's URL is configuration, not a credential: its secret is the signing key.
  if (channel.kind === "webhook" && channel.config.url) return channel.config.url;
  return channel.destination ?? "—";
}

/* ---------------------------------------------------------- server problems */

/**
 * The API's field errors, placed on this form's fields. `map` turns an API field
 * (`condition.check_ids`, `secret`) into one of the form's, or undefined when the form
 * has no such field — in which case the refusal is shown whole instead.
 */
export function fieldProblems<F extends string>(
  error: unknown,
  map: (apiField: string) => F | undefined,
): Partial<Record<F, string>> {
  if (!(error instanceof ApiError)) return {};
  const reported = error.problem?.errors;
  if (!Array.isArray(reported)) return {};
  const problems: Partial<Record<F, string>> = {};
  for (const item of reported) {
    if (typeof item !== "object" || item === null) continue;
    const { field, reason } = item as Record<string, unknown>;
    if (typeof field !== "string" || typeof reason !== "string") continue;
    const placed = map(field);
    if (placed === undefined) continue;
    const sentence = reason.charAt(0).toUpperCase() + reason.slice(1);
    problems[placed] = problems[placed] ? `${problems[placed]} ${sentence}.` : `${sentence}.`;
  }
  return problems;
}

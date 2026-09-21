import type { AuditEntry } from "./api";

/**
 * An audit entry as a sentence: *Raj changed Priya's role from Member to Viewer.*
 *
 * The API stores an action and a target; the words are the screen's. An action this
 * does not know still reads, as its own name, so a new kind of event is never hidden.
 */

export function actorName(entry: AuditEntry): string {
  if (entry.actor.kind === "staff") return `nexovue support (${entry.actor.name ?? "staff"})`;
  if (entry.actor.kind === "system") return "nexovue";
  return entry.actor.name ?? "someone no longer here";
}

function role(value: unknown): string {
  return typeof value === "string" && value.length > 0
    ? `${value[0]?.toUpperCase()}${value.slice(1)}`
    : "—";
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "—";
}

export function describeEntry(entry: AuditEntry): string {
  const who = actorName(entry);
  const whom = entry.target_name ?? text(entry.detail?.email);
  const detail = entry.detail ?? {};
  switch (entry.action) {
    case "member.invited":
      return `${who} invited ${text(detail.email)} as ${role(detail.role)}`;
    case "member.invite_resent":
      return `${who} resent the invite to ${text(detail.email)}`;
    case "member.invite_withdrawn":
      return `${who} withdrew the invite to ${text(detail.email)}`;
    case "member.joined":
      return `${who} joined as ${role(detail.role)}`;
    case "member.role_changed":
      return `${who} changed ${whom}'s role from ${role(detail.before)} to ${role(detail.after)}`;
    case "member.removed":
      return `${who} removed ${text(detail.email)} (${role(detail.role)})`;
    case "session.created":
      return `${who} signed in`;
    case "session.ended":
      return `${who} signed out`;
    case "session.denied":
      return "A sign-in was refused";
    default:
      return `${who}: ${entry.action.replaceAll("_", " ").replace(".", " — ")}`;
  }
}

export const KIND_LABELS: readonly { id: string; label: string }[] = [
  // **First, and the default.** Seventeen consecutive "signed in" rows buried
  // the events the log exists for. This hides nothing — "Everything" is one
  // click away and still holds every row — it only stops the screen opening on
  // the least interesting thing in it.
  { id: "changes", label: "Changes only" },
  { id: "", label: "Everything" },
  { id: "people", label: "People" },
  { id: "credentials", label: "Keys and secrets" },
  { id: "sign_in", label: "Sign-in and security" },
  { id: "configuration", label: "Configuration" },
  { id: "support", label: "nexovue support" },
];

export const RANGES: readonly { id: string; label: string; hours: number | null }[] = [
  { id: "24h", label: "Last 24 hours", hours: 24 },
  { id: "7d", label: "Last 7 days", hours: 24 * 7 },
  { id: "30d", label: "Last 30 days", hours: 24 * 30 },
  { id: "all", label: "All time", hours: null },
];

import { formatRelativeTime } from "@/shared/format/time";
import { formatZonedTime } from "@/shared/format/zoned";

import type { CaseLink, CaseSignal, TimelineEvent } from "./api";

/**
 * What a case's tracker issue says on the finding page (F22 T4.7; spec, *Link and
 * connection states*, *Words after an action*), apart from how it is drawn, so each
 * status, reader and age is a tested row. Words change with age on the caller's clock
 * (AC119); a failure reads by reader (FR44, AC116). Nothing a tracker wrote is shown:
 * only its key, built address and status.
 */

const MS_PER_MINUTE = 60_000;
const MINUTES_PER_HOUR = 60;
/** "Creating" turns into "Not created yet" after this (spec, *Link states*). */
const CREATING_PATIENCE_MINUTES = 2;
/** An active case's link is read every 5 minutes; past this, it says it is late. */
const SYNC_LATE_MINUTES = 30;

const TRACKER_NAMES: Record<string, string> = { github: "GitHub", jira: "Jira" };
const ASK_AN_ADMIN = "Ask an admin to reconnect GitHub. The case still works.";

export interface LinkReader {
  /** Whether the case is active: only then is a late sync worth saying. */
  caseActive: boolean;
  /** An owner or admin reads the fix; others read whom to ask (FR44). */
  admin: boolean;
  now?: number;
  zone?: string;
}

interface WithLinks {
  links?: CaseLink[];
}

/** The case's one live synced issue: not a reference, not stopped (FR41). */
export function syncedLink(root: WithLinks): CaseLink | null {
  return (
    (root.links ?? []).find((link) => link.role === "synced" && link.sync_status !== "stopped") ??
    null
  );
}

function trackerName(link: CaseLink): string {
  return TRACKER_NAMES[link.kind ?? "github"] ?? "the tracker";
}

function minutesSince(iso: string | null, now: number): number | null {
  if (iso == null) return null;
  const at = Date.parse(iso);
  return Number.isNaN(at) ? null : (now - at) / MS_PER_MINUTE;
}

function lateWords(minutes: number): string {
  if (minutes < MINUTES_PER_HOUR) return `Not synced for ${Math.floor(minutes)} minutes`;
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  return `Not synced for ${hours} hour${hours === 1 ? "" : "s"}`;
}

/** Why a link needs a person, in an admin's words: what broke and the fix (FR44). */
function attentionForAdmin(link: CaseLink): string {
  switch (link.last_failure) {
    case "repository_unreachable":
      return `nexovue can no longer read ${link.place_key ?? "this repository"}. Check the App's repositories in GitHub, then refresh the connection in settings.`;
    case "moved_out_of_reach":
      return "This issue moved to a repository nexovue does not reach. Add that repository to the App in GitHub.";
    case "write_refused":
      return `${trackerName(link)} refused a change to this issue. Check the App's permissions in GitHub, then retry.`;
    case "unauthorized":
      return "GitHub refuses the App's key. Replace it in settings.";
    default:
      return "This issue needs attention in settings.";
  }
}

/** What a reopen another case blocks says, to everyone: nobody need act (row 7). */
const REOPEN_BLOCKED = `Reopened in GitHub, but another case is active on this service, so this one stays resolved.`;

/** One link's words, now, led by its key where it has one (spec, *Link states*). */
export function linkSentence(link: CaseLink, reader: LinkReader): string {
  const words = linkStatusWords(link, reader);
  return link.external_key == null ? words : `${link.external_key} · ${words}`;
}

/** One link's status in words, now, without its key, for a list that shows the key. */
export function linkStatusWords(link: CaseLink, reader: LinkReader): string {
  const now = reader.now ?? Date.now();
  switch (link.sync_status) {
    case "creating": {
      const waited = minutesSince(link.created_at, now) ?? 0;
      return waited < CREATING_PATIENCE_MINUTES
        ? "Creating the issue. This may take a moment"
        : "Not created yet. nexovue cannot reach its sync service. The case still works.";
    }
    case "promoting":
      return `Checking this repository before syncing it`;
    case "in_sync": {
      const since = minutesSince(link.last_synced_at, now);
      if (since == null) return `waiting for its first sync`;
      if (reader.caseActive && since > SYNC_LATE_MINUTES) return `${lateWords(since)}`;
      return `synced ${formatRelativeTime(link.last_synced_at ?? "", now)}`;
    }
    case "waiting": {
      const last =
        link.last_synced_at == null
          ? "Not synced yet"
          : `Last synced ${formatRelativeTime(link.last_synced_at, now)}`;
      return `Could not reach ${trackerName(link)}. Trying again. ${last}`;
    }
    case "needs_attention":
      if (link.last_failure === "reopen_blocked") return REOPEN_BLOCKED;
      return `${reader.admin ? attentionForAdmin(link) : ASK_AN_ADMIN}`;
    case "refused":
      if (link.last_failure === "public_repository") {
        return reader.admin
          ? "Not created: the repository is public. An admin can allow it in settings."
          : "Not created: the repository is public. Ask an admin.";
      }
      return "Not created: the stored facts could not be read. Create it again.";
    case "gone":
      return `Cannot find this issue. It may be deleted or moved`;
    case "not_followed": {
      const since =
        link.last_synced_at == null
          ? "the case resolved"
          : formatZonedTime(link.last_synced_at, { zone: reader.zone, now });
      return `No longer followed since ${since}. This issue may have changed. Reopen the case to follow it again`;
    }
    case "stopped":
      return `GitHub was disconnected. This issue is no longer followed.`;
  }
}

interface ResolvingCase extends WithLinks {
  case_signal: CaseSignal;
}

export type ResolveChoice =
  | { kind: "one_tap" }
  | { kind: "signal_open"; sentences: string[] }
  | { kind: "synced"; issueKey: string; sentences: string[] }
  | { kind: "held"; issueKey: string; sentences: string[] };

const SIGNAL_STILL_OPEN = "The signal is still open: the problem is still being measured.";

function isHeld(link: CaseLink): boolean {
  return link.tracker_closed_at != null || link.seen_state === "closed";
}

/**
 * FR6, AC92: what Resolve asks first. With a synced issue it always confirms, naming
 * it, and offers to leave it open; when the issue is already closed it says nothing
 * more goes there, with one button. With no issue and the signal closed, one tap.
 */
export function resolveChoice(root: ResolvingCase): ResolveChoice {
  const link = syncedLink(root);
  const signal = root.case_signal === "open" ? [SIGNAL_STILL_OPEN] : [];
  if (link?.external_key == null) {
    return signal.length > 0 ? { kind: "signal_open", sentences: signal } : { kind: "one_tap" };
  }
  const issueKey = link.external_key;
  if (isHeld(link)) {
    return {
      kind: "held",
      issueKey,
      sentences: [
        ...signal,
        `${issueKey} is already closed in ${trackerName(link)}. Nothing more goes there.`,
      ],
    };
  }
  return {
    kind: "synced",
    issueKey,
    sentences: [...signal, `This closes ${issueKey} and adds one comment there.`],
  };
}

export type IssueOutcome = "will_close" | "left_open" | "already_closed";

/** The line after a resolve with a synced issue (spec, *Words after an action*). */
export function resolveDoneSentence(issueKey: string, outcome: IssueOutcome): string {
  if (outcome === "left_open") return `Resolved here. ${issueKey} stays open.`;
  if (outcome === "already_closed") {
    return `Resolved. ${issueKey} was already closed, so nothing changes there.`;
  }
  return `Resolved. ${issueKey} will be closed when sync next runs. The link below shows when it has.`;
}

/** D73: the page's line while the issue is closed and the signal is open (AC93). */
export function holdLine(root: WithLinks, zone?: string, now?: number): string | null {
  const link = syncedLink(root);
  if (link?.tracker_closed_at == null) return null;
  const at = formatZonedTime(link.tracker_closed_at, { zone, now });
  return `Issue closed in ${trackerName(link)} at ${at}, and the signal is still open.`;
}

/** FR7, AC58: a false alarm on the root names the issue it closes. */
export function falseAlarmIssueSentence(root: WithLinks): string | null {
  const link = syncedLink(root);
  if (link?.external_key == null) return null;
  if (isHeld(link)) {
    return `${link.external_key} is already closed in ${trackerName(link)}. Nothing more goes there.`;
  }
  return `This also closes ${link.external_key} and adds one comment there.`;
}

const STATUS_WORDS: Record<CaseLink["sync_status"], string> = {
  creating: "creating",
  promoting: "being checked",
  in_sync: "in sync",
  waiting: "waiting",
  needs_attention: "in need of attention",
  refused: "refused",
  gone: "gone",
  not_followed: "no longer followed",
  stopped: "stopped",
};

/**
 * FR45, AC105: the status region announces a link's status changing, never its age:
 * a newer sync time alone says nothing.
 */
export function linkAnnouncement(before: CaseLink[], after: CaseLink[]): string | null {
  const was = new Map(before.map((link) => [link.id, link.sync_status]));
  const changes = after
    .filter((link) => was.get(link.id) !== link.sync_status)
    .map((link) => `${link.external_key ?? "An issue"} is now ${STATUS_WORDS[link.sync_status]}.`);
  return changes.length > 0 ? changes.join(" ") : null;
}

/**
 * FR51, AC127: the person's own notes on the case, oldest first, for their clipboard
 * only. Nothing here sends them anywhere.
 */
export function myNotesText(events: TimelineEvent[], viewerId: string | null): string {
  if (viewerId == null) return "";
  return events
    .filter(
      (event) =>
        event.kind === "note_added" && event.actor_id === viewerId && event.note_body != null,
    )
    .sort((a, b) => a.at.localeCompare(b.at))
    .map((event) => event.note_body)
    .join("\n\n");
}

const CLOSE_REASON_WORDS: Record<string, string> = {
  completed: "completed",
  not_planned: "not planned",
  duplicate: "a duplicate",
  other: "closed",
};

const FAILURE_WORDS: Record<string, string> = {
  public_repository: "the repository is public",
  write_refused: "the tracker refused it",
};

/** Timeline rows about the issue (FR16), from kinds and enums alone. */
export function issueEventSentence(event: TimelineEvent): string | null {
  const who = event.actor_kind === "person" ? (event.actor_name ?? "Former member") : null;
  const detail = event.detail ?? {};
  const reason = CLOSE_REASON_WORDS[String(detail.close_reason ?? "other")] ?? "closed";
  switch (event.kind) {
    case "link_added":
      return `${who ?? "Someone"} asked for an issue`;
    case "link_removed":
      return `${who ?? "Someone"} removed the issue here`;
    case "link_promoted":
      return `${who ?? "Someone"} chose to sync this issue`;
    case "issue_created":
      return "The issue was created";
    case "issue_closed":
      return event.actor_kind === "tracker"
        ? `The issue was closed in the tracker, as ${reason}`
        : `nexovue closed the issue, as ${reason}`;
    case "issue_reopened":
      return event.actor_kind === "tracker"
        ? "The issue was reopened in the tracker"
        : "nexovue reopened the issue";
    case "issue_gone":
      return "The issue cannot be found";
    case "issue_moved":
      return "The issue moved";
    case "tracker_write_failed": {
      const failure = FAILURE_WORDS[String(detail.failure)] ?? "the tracker refused it";
      return detail.failure === "public_repository"
        ? `The issue was not created: ${failure}`
        : `A change to the issue did not go through: ${failure}`;
    }
    default:
      return null;
  }
}

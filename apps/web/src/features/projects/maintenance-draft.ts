import { ApiError } from "@/shared/api-client";
import { plural } from "@/shared/format/plural";
import { fromLocalInput, toLocalInput } from "@/shared/local-instant";

import type {
  CheckChoice,
  MaintenanceWindow,
  MaintenanceWindowRequest,
  Recurrence,
} from "./check-settings-api";

/**
 * A maintenance window as its form holds it, and the way back to what the API takes.
 *
 * Times are `datetime-local` text in the reader's own zone; the API takes instants.
 * The conversion lives in `shared/local-instant.ts` and is only called from here.
 */

export interface MaintenanceDraft {
  name: string;
  /** `datetime-local` text, in the browser's zone. */
  startsAt: string;
  endsAt: string;
  recurrence: Recurrence;
  /** Kept while a window is switched to "does not repeat", so switching back restores it. */
  repeatUntil: string;
  appliesToAll: boolean;
  checkIds: string[];
}

export type DraftField = "name" | "starts_at" | "ends_at" | "repeat_until" | "check_ids";
export type DraftProblems = Partial<Record<DraftField, string>>;

/** How the form's summary names each field. */
export const DRAFT_FIELD_LABELS: Record<DraftField, string> = {
  name: "Name",
  starts_at: "Starts",
  ends_at: "Ends",
  repeat_until: "Repeat until",
  check_ids: "Covers",
};

export const MAX_WINDOW_NAME_LENGTH = 120;

const MS_PER_DAY = 86_400_000;
const PERIOD_MS: Record<Exclude<Recurrence, "none">, number> = {
  daily: MS_PER_DAY,
  weekly: 7 * MS_PER_DAY,
};
const PERIOD_WORD: Record<Exclude<Recurrence, "none">, string> = { daily: "day", weekly: "week" };

/** Whole project by default: the common case, and it covers checks created later. */
export function emptyDraft(): MaintenanceDraft {
  return {
    name: "",
    startsAt: "",
    endsAt: "",
    recurrence: "none",
    repeatUntil: "",
    appliesToAll: true,
    checkIds: [],
  };
}

export function draftOf(stored: MaintenanceWindow): MaintenanceDraft {
  return {
    name: stored.name,
    startsAt: toLocalInput(stored.starts_at),
    endsAt: toLocalInput(stored.ends_at),
    recurrence: stored.recurrence,
    repeatUntil: stored.repeat_until === null ? "" : toLocalInput(stored.repeat_until),
    appliesToAll: stored.applies_to_all,
    checkIds: stored.applies_to_all ? [] : [...stored.check_ids],
  };
}

/**
 * What the draft says once the fields that no longer apply are dropped: no end date
 * on a window that does not repeat, no checks on one that covers the whole project.
 * The API refuses both combinations, and the form hides both fields when they apply.
 */
function normalised(draft: MaintenanceDraft) {
  const repeating = draft.recurrence !== "none";
  return {
    name: draft.name.trim(),
    startsAt: draft.startsAt,
    endsAt: draft.endsAt,
    recurrence: draft.recurrence,
    repeatUntil: repeating ? draft.repeatUntil : "",
    appliesToAll: draft.appliesToAll,
    checkIds: draft.appliesToAll ? [] : [...new Set(draft.checkIds)].sort(),
  };
}

/** Undefined while any time cannot be read — `draftProblems` says which. */
export function requestOf(
  draft: MaintenanceDraft,
  projectId: string,
): MaintenanceWindowRequest | undefined {
  const plain = normalised(draft);
  const startsAt = fromLocalInput(plain.startsAt);
  const endsAt = fromLocalInput(plain.endsAt);
  const repeatUntil = plain.repeatUntil === "" ? null : fromLocalInput(plain.repeatUntil);
  if (startsAt === null || endsAt === null) return undefined;
  if (plain.repeatUntil !== "" && repeatUntil === null) return undefined;

  return {
    project_id: projectId,
    name: plain.name,
    starts_at: startsAt,
    ends_at: endsAt,
    recurrence: plain.recurrence,
    repeat_until: repeatUntil,
    applies_to_all: plain.appliesToAll,
    check_ids: plain.checkIds,
  };
}

/** The API's rules in `maintenance/service.py`, plus the fields it cannot receive empty. */
export function draftProblems(draft: MaintenanceDraft): DraftProblems {
  const plain = normalised(draft);
  const problems: DraftProblems = {};

  if (plain.name === "") problems.name = "Name the window.";
  else if (plain.name.length > MAX_WINDOW_NAME_LENGTH) {
    problems.name = `At most ${MAX_WINDOW_NAME_LENGTH} characters.`;
  }

  const start = instantOf(plain.startsAt);
  const end = instantOf(plain.endsAt);
  if (start === undefined) problems.starts_at = "Choose when it starts.";
  if (end === undefined) problems.ends_at = "Choose when it ends.";

  if (start !== undefined && end !== undefined) {
    if (end <= start) problems.ends_at = "Must be after the start.";
    else if (plain.recurrence !== "none" && end - start >= PERIOD_MS[plain.recurrence]) {
      const word = PERIOD_WORD[plain.recurrence];
      problems.ends_at = `A ${plain.recurrence} window must be shorter than one ${word}, or each one runs into the next.`;
    }
  }

  if (plain.repeatUntil !== "") {
    const until = instantOf(plain.repeatUntil);
    if (until === undefined) problems.repeat_until = "Choose a date and time, or leave it empty.";
    // Earlier than the first start, the window would never take effect at all.
    else if (start !== undefined && until < start) {
      problems.repeat_until = "Must not be before the first start.";
    }
  }

  if (!plain.appliesToAll && plain.checkIds.length === 0) {
    problems.check_ids = "Choose at least one check, or cover the whole project.";
  }

  return problems;
}

function instantOf(localText: string): number | undefined {
  const iso = fromLocalInput(localText);
  return iso === null ? undefined : Date.parse(iso);
}

/**
 * Whether the draft still says what is stored — the unsaved-changes test. A field
 * the form hides, or checks ticked in another order, is not a change.
 */
export function sameWindow(
  draft: MaintenanceDraft,
  stored: MaintenanceWindow | undefined,
): boolean {
  const left = normalised(draft);
  const right = normalised(stored === undefined ? emptyDraft() : draftOf(stored));
  return (
    left.name === right.name &&
    left.startsAt === right.startsAt &&
    left.endsAt === right.endsAt &&
    left.recurrence === right.recurrence &&
    left.repeatUntil === right.repeatUntil &&
    left.appliesToAll === right.appliesToAll &&
    left.checkIds.join() === right.checkIds.join()
  );
}

const DRAFT_FIELDS: readonly DraftField[] = [
  "name",
  "starts_at",
  "ends_at",
  "repeat_until",
  "check_ids",
];
const LOCATION_PREFIX = /^(body|query|path)\./;

function isDraftField(field: string): field is DraftField {
  return (DRAFT_FIELDS as readonly string[]).includes(field);
}

/**
 * The API's 422 `errors`, placed under the fields they name. Several reasons for one
 * field — two unknown checks — are all kept, because fixing one and meeting the next
 * on the following save is a form arguing with its reader.
 */
export function serverProblems(error: unknown): DraftProblems {
  if (!(error instanceof ApiError)) return {};
  const reported = error.problem?.errors;
  if (!Array.isArray(reported)) return {};

  const byField: Partial<Record<DraftField, string[]>> = {};
  for (const item of reported) {
    if (typeof item !== "object" || item === null) continue;
    const { field, reason } = item as Record<string, unknown>;
    if (typeof field !== "string" || typeof reason !== "string") continue;
    const name = field.replace(LOCATION_PREFIX, "");
    if (!isDraftField(name)) continue;
    byField[name] = [...(byField[name] ?? []), sentence(reason)];
  }

  const problems: DraftProblems = {};
  for (const field of DRAFT_FIELDS) {
    const reasons = byField[field];
    if (reasons) problems[field] = reasons.join(" ");
  }
  return problems;
}

/** The API words its reasons as clauses: "must be after the start". */
function sentence(reason: string): string {
  const trimmed = reason.trim();
  const capitalised = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capitalised) ? capitalised : `${capitalised}.`;
}

/* -------------------------------------------------------------- reading one */

export function describeRecurrence(recurrence: Recurrence, untilLabel: string | undefined): string {
  if (recurrence === "none") return "Does not repeat";
  const word = PERIOD_WORD[recurrence];
  return untilLabel === undefined
    ? `Repeats every ${word}, with no end date`
    : `Repeats every ${word} until ${untilLabel}`;
}

/** Names when the checks are known; a count when they are not loaded or no longer exist. */
export function describeScope(
  scheduled: Pick<MaintenanceWindow, "applies_to_all" | "check_ids">,
  checks: readonly CheckChoice[] | undefined,
): string {
  if (scheduled.applies_to_all) return "Every check in this project, including checks added later";

  const count = scheduled.check_ids.length;
  const counted = `${count} ${plural(count, "check")}`;
  if (checks === undefined) return counted;

  const names = scheduled.check_ids.map((id) => checks.find((check) => check.id === id)?.name);
  if (names.some((name) => name === undefined)) return counted;
  return `${counted}: ${names.join(", ")}`;
}

/**
 * Every word the monthly answer says (F19 spec FR27, FR27a, FR27b, FR27c, FR30).
 *
 * A month's figures come from the API, summed by the platform's one function from the
 * target's stored days. Here they become sentences, the same sentence on the screen and
 * in the copied text, so a month copied into a note keeps what it leaves out and its
 * fixed label (AC78). The label itself is the API's (`report_label`), the CSV's too, so
 * the two cannot differ (D42).
 *
 * `test_slo_words.py` scans this folder for words the spec forbids (AC13).
 */

import type {
  MonthResult,
  Sampling,
  SloHistoryEntry,
  SloKind,
  SloLastMonth,
  SloMonth,
  SloMonthRow,
  SloOutage,
  SloSharpFall,
  SloView,
} from "./api";
import { durationOf } from "./slo-words";

/** FR27: months build up to 13, and the CSV reaches as far (founder decision 20). */
export const MONTHS_KEPT = 13;

const PERCENT = 100;

export const RESULT_WORDS: Record<MonthResult, string> = {
  met: "Met",
  missed: "Missed",
  not_judged: "Not judged",
};

/** FR27b, D47: why the view gives no single figure, and why one project. */
export const NO_SINGLE_FIGURE =
  "There is no single figure across targets: each counts different requests against its own objective, so an average would hide the one that missed.";
export const ONE_PROJECT =
  "These are this project's targets. A project is one environment, so each project reads its own.";

/** A month as "October 2026". Dates are read as calendar dates, never shifted by a zone. */
export function monthName(month: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${month}T00:00:00Z`));
}

/** A day as "14 Oct". */
export function shortDay(day: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(new Date(`${day.slice(0, 10)}T00:00:00Z`));
}

/** A moment's day in the project's zone, as "3 Nov". */
export function dayIn(iso: string, zone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: zone,
  }).format(new Date(iso));
}

function measured(month: SloMonth): number | null {
  return month.coverage === null ? null : Math.floor(month.coverage * PERCENT);
}

function objectiveOf(month: SloMonth): string {
  return `${Number(month.objective_percent)}%`;
}

/** FR27's leading sentence, with quiet and excluded time always stated (FR27b). */
export function monthSentence(month: SloMonth, kind: SloKind): string {
  const name = monthName(month.month);
  const share = measured(month);
  if (month.result === "not_judged") {
    return share === null || month.total === 0
      ? `${name}: not judged. Nothing was counted.`
      : `${name}: not judged. Nexovue measured only ${share}%.`;
  }
  const parts = [`${name}: ${month.figure_percent}% against ${objectiveOf(month)}.`];
  if (kind === "check_availability") {
    parts.push(`Measured ${share}% of the expected runs.`);
    parts.push(`${month.missed_runs ?? 0} runs missed.`);
  } else {
    parts.push(`Measured ${share}% of the month.`);
    parts.push(
      month.quiet_minutes > 0
        ? `${durationOf(month.quiet_minutes)} with no requests, the longest ${durationOf(month.longest_quiet_minutes)}.`
        : "No minute without requests.",
    );
  }
  if (month.lost_minutes > 0) {
    parts.push(`${durationOf(month.lost_minutes)} may have been lost by nexovue.`);
  }
  parts.push(
    month.excluded_minutes > 0
      ? `${durationOf(month.excluded_minutes)} left out for planned work.`
      : "Nothing left out for planned work.",
  );
  return parts.join(" ");
}

const CHANGED_BY: Record<NonNullable<SloMonth["changed_why"]>, string> = {
  definition: "a change to what it counts",
  exclusion: "an exclusion",
  exclusion_removed: "an exclusion being removed",
};

/** FR27a: the outage the figure does not count, with its whole length. */
export function outageLine(outage: SloOutage, zone: string): string {
  return `This figure does not count the outage on ${dayIn(outage.started_at, zone)} (${durationOf(outage.minutes)}). Requests that never arrived are not failures.`;
}

/** FR27c. */
export function sharpFallLine(fall: SloSharpFall): string {
  return `On ${shortDay(fall.day)} requests were ${fall.fall_percent}% below the same weekday in earlier weeks. Requests rejected before reaching this service are not failures.`;
}

/** FR27's other lines under a month: what still moves, what changed, what it holds. */
export function monthNotes(month: SloMonth, kind: SloKind, zone: string): string[] {
  const notes: string[] = [];
  if (month.may_still_change)
    notes.push("May still change: some of its days are under 7 days old.");
  if (month.changed_at && month.changed_why) {
    notes.push(`Changed on ${dayIn(month.changed_at, zone)} by ${CHANGED_BY[month.changed_why]}.`);
  }
  if (month.days_held < month.days_in_month) {
    notes.push(`Holds ${month.days_held} of ${month.days_in_month} days.`);
  }
  if (month.earlier_definition_through) {
    notes.push(
      `Days up to ${shortDay(month.earlier_definition_through)} were counted under the earlier definition.`,
    );
  }
  if (!month.loss_known) {
    notes.push(
      "Loss not known for some days: they were first read after nexovue's record of what it lost had gone.",
    );
  }
  if (kind === "check_availability" && month.maintenance_runs) {
    notes.push(
      `Runs in maintenance windows are not counted (${month.maintenance_runs} runs this month).`,
    );
  }
  notes.push(...month.sharp_falls.map(sharpFallLine));
  return notes;
}

/** FR5: said wherever a figure leaves the screen, when the answer was Yes. */
export const SAMPLED_LINE = "Sampled before reaching nexovue, so these figures count the sample.";

/** FR27b, AC78: the copied month: label, period and what it covers, quiet and excluded
 * time, outages, the sampling answer when Yes, the fixed label and who it is for. */
export function copyTextOf(input: {
  label: string;
  month: SloMonth;
  kind: SloKind;
  sampling: Sampling | null;
  reportLabel: string;
  audience: string;
  zone: string;
}): string {
  const { month } = input;
  const lines = [
    `${input.label}.`,
    monthSentence(month, input.kind),
    `${RESULT_WORDS[month.result]}.`,
    ...month.outages.map((outage) => outageLine(outage, input.zone)),
    ...monthNotes(month, input.kind, input.zone),
  ];
  if (input.sampling === "yes") lines.push(SAMPLED_LINE);
  lines.push(input.reportLabel, input.audience);
  return lines.join(" ");
}

/** FR27's last line: a month and the rolling window that disagree are different periods. */
export function differentPeriodsOf(view: SloView, month: SloMonth): string | null {
  if (month.result === "not_judged" || view.figures.figure_percent === null) return null;
  const windowMet = view.figures.missed_by === 0;
  if (windowMet === (month.result === "met")) return null;
  const name = new Intl.DateTimeFormat("en-GB", { month: "long", timeZone: "UTC" }).format(
    new Date(`${month.month}T00:00:00Z`),
  );
  return `The last ${view.window_days} days and ${name} are different periods.`;
}

/** FR27b: "5 of 7 targets met in October. 1 missed. 1 not judged. 2 of those met have
 * time not counted." No blended figure. */
export function lastMonthHeadline(last: SloLastMonth): string {
  const name = new Intl.DateTimeFormat("en-GB", { month: "long", timeZone: "UTC" }).format(
    new Date(`${last.month}T00:00:00Z`),
  );
  const parts = [
    `${last.met} of ${last.targets} targets met in ${name}.`,
    `${last.missed} missed.`,
    `${last.not_judged} not judged.`,
  ];
  if (last.met_with_time_not_counted > 0) {
    parts.push(
      `${last.met_with_time_not_counted} of those met ${last.met_with_time_not_counted === 1 ? "has" : "have"} time not counted.`,
    );
  }
  return parts.join(" ");
}

/** What a *Last month* row leaves out, in a few words. */
export function leavesOutOf(row: SloMonthRow): string {
  const parts: string[] = [];
  if (row.kind === "check_availability") {
    if (row.missed_runs) parts.push(`${row.missed_runs} runs missed`);
  } else if (row.quiet_minutes > 0) {
    parts.push(`${durationOf(row.quiet_minutes)} with no requests`);
  }
  if (row.lost_minutes > 0) parts.push(`${durationOf(row.lost_minutes)} may have been lost`);
  if (row.excluded_minutes > 0) parts.push(`${durationOf(row.excluded_minutes)} left out`);
  if (row.outages.length > 0) {
    parts.push(`${row.outages.length} ${row.outages.length === 1 ? "outage" : "outages"}`);
  }
  if (row.days_held < row.days_in_month)
    parts.push(`${row.days_held} of ${row.days_in_month} days`);
  return parts.length > 0 ? `${parts.join(", ")}.` : "Nothing.";
}

const FIELD_WORDS: Record<string, string> = {
  name: "Name",
  description: "Description",
  service_name: "Service",
  endpoint_name: "Endpoint",
  check_id: "Check",
  boundary_index: "Threshold",
  asked_ms: "Threshold asked for, ms",
  left_out_endpoints: "Endpoints left out",
  objective_percent: "Target",
  window_days: "Window, days",
  upstream_sampling: "Sampled before nexovue",
  enabled: "Alerts on",
};

const WHAT_WORDS: Record<SloHistoryEntry["what"], string> = {
  created: "Created",
  changed: "Changed",
  deleted: "Deleted",
  restored: "Restored",
};

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length > 0 ? value.join(", ") : "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

/** FR30: who did what, and when; a name only where the API gave one. */
export function historyHeading(entry: SloHistoryEntry): string {
  return entry.by_name ? `${WHAT_WORDS[entry.what]} by ${entry.by_name}` : WHAT_WORDS[entry.what];
}

/** FR30: each field a change moved, from what to what. */
export function historyChanges(entry: SloHistoryEntry): string[] {
  if (entry.what === "restored" && entry.after?.name) return [`Name: ${shown(entry.after.name)}`];
  if (entry.what !== "changed" || !entry.before || !entry.after) return [];
  const before = entry.before;
  const after = entry.after;
  return Object.keys(after)
    .filter((key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]))
    .map((key) => `${FIELD_WORDS[key] ?? key}: ${shown(before[key])} → ${shown(after[key])}`);
}

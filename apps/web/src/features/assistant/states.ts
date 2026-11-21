import type { Label, MessageState, MessageView, Phase, StandingState } from "./api";

/**
 * Every sentence the panel shows of its own, from the design's *Words* table and the spec's
 * *Conversation states* (F4). One place, so a test can hold each state to its words. Model
 * words never come from here: they arrive in an answer and render as plain text.
 */

export const LABEL_WORDS: Record<Label, string> = {
  counted: "Counted from telemetry",
  checked: "nexovue's own check",
  text: "Your telemetry says this; nexovue has not measured it",
  team: "From your team's notes",
  unchecked:
    "The model's wording: it read these steps, and this sentence was not checked against them",
};
/** The notes label with its age (design, *Words*): "From your team's notes, {age}". */
export const teamLabel = (age: string) => `${LABEL_WORDS.team}, ${age}`;
export const CAUSE_WARNING = "nexovue has not measured a cause";
export const REMOVED_ONE = "One sentence was removed because it cited nothing it had read.";
export const removedMany = (count: number) =>
  `${count} sentences were removed because they cited nothing they had read.`;
export const NOT_VERIFIED = "Not verified";
/** Sentences removed for telling the reader to act on what they quote (check 5). */
export const REMOVED_FOR_ACTING_ONE =
  "One sentence was removed because it told you to act on quoted text.";
export const removedForActing = (count: number) =>
  count === 1
    ? REMOVED_FOR_ACTING_ONE
    : `${count} sentences were removed because they told you to act on quoted text.`;
export const FIGURE_FLAG = "Not found in what it read. Check this number.";
export const QUOTE_FOLDED =
  "Text from a log, written by anyone who can reach your service. Do not run it.";
export const KNOWLEDGE_TOOL = "search_knowledge";
/** A runbook quote's label (design, *Words*): "From a runbook your team wrote, {age}". */
export const runbookQuoteLabel = (age: string) => `From a runbook your team wrote, ${age}`;
export const RUNBOOK_QUOTE_CUT = "Cut at 500 characters. Open the runbook for all of it";
export const OPEN_RUNBOOK = "Open the runbook";
/** What the runbook quote's Copy control names, read as "Copy this runbook text". */
export const COPY_QUOTE = "this runbook text";
/** FR42: knowledge search's empty answer, so absence does not read as "it never happened". */
/** Beside a knowledge step that searched by keyword alone (FR27, AC41). */
export const KEYWORD_ONLY_STEP = "Searched by keyword only.";
export const noKnowledgeRecord = (days: number | null) =>
  days === null
    ? "No record of this in what is kept."
    : `No record of this in what is kept. Cases are kept for ${days} ${days === 1 ? "day" : "days"} after they end.`;
export const QUOTE_SOURCE: Record<string, string> = {
  search_logs: "From a log line",
  search_knowledge: "From a fact nexovue recorded",
  list_error_groups: "From an error message",
  get_error_group: "From an error message",
};
export const QUOTE_GONE: Record<string, string> = {
  search_logs: "This log line is no longer available.",
  list_error_groups: "This error message is no longer available.",
  get_error_group: "This error message is no longer available.",
  search_knowledge: "This runbook was deleted, or is no longer kept.",
};
export const QUOTE_READ_FAILED = "Could not read this now";
export const QUOTE_CUT = "Cut at 500 characters. Open what it read for all of it.";
export const QUOTE_LOADING = "Reading the quote…";
export const SHOW_FOLDED = "Show it";
export const NO_ROWS = "No matching rows in what was stored";
export const agedOut = (date: string) => `The data before ${date} is no longer kept.`;
export const latestNoTimeFilter = (rows: number) => `Latest ${rows}, no time filter`;
export const READ_REFUSED_FOR_NOW = "The read was refused for now.";
export const COPY_STEPS = "Copy steps";
export const WRONG_MARKED = "Marked wrong. Admins see only a count; no AI learns from it.";
export const LOST_CONTACT = "Lost contact; this question may still be running.";
export const CHECK_IT_YOURSELF = "Check it yourself";
export const ASK_AGAIN = "Ask again";
export const SLOW_WAIT = "Taking longer than usual";
/** Past this, a queued question says so, with a way to look yourself (spec, *States*). */
export const SLOW_WAIT_SECONDS = 20;
export const USES_ONLY_READS = "This answer uses only the reads listed.";
export const deleteWarning = (count: number) =>
  `Deletes ${count} ${count === 1 ? "question and its answer" : "questions and their answers"}. This cannot be undone.`;
export const DELETED = "Deleted.";
export const ANSWERING = "Answering…";
export const ANSWER_READY = "Answer ready";

export const STANDING_WORDS: Record<
  Exclude<StandingState, "ready">,
  (manage: boolean) => string
> = {
  ai_off: (manage) =>
    manage
      ? "Chat is not on here. Turn AI on in AI settings."
      : "Chat is not on here. Ask an admin to turn this on.",
  chat_off: (manage) =>
    manage
      ? "Chat is off. Turn it on in AI settings, under Chat."
      : "Chat is not on here. Ask an admin to turn this on.",
  no_consent: () => "Chat is waiting for an owner to agree to what it sends.",
  opted_out: () => "AI is off for this project.",
  no_data: () => "This project has sent no telemetry yet, so chat has nothing to read.",
};
export const SEND_DATA = "Set up sending data";
export const EVERYTHING_ELSE_WORKS = "Every other screen works as usual.";

export function waitingFor(names: string[]): string {
  return names.length === 0
    ? STANDING_WORDS.no_consent(false)
    : `Chat is waiting for ${names.join(", ")} to agree.`;
}

const PHASE_WORDS: Record<Phase, string> = {
  waiting: "Waiting for a worker",
  asking: "Asking your AI provider",
  reading: "Reading",
  checking: "Checking the answer",
};

export function phaseLine(phase: Phase | null, seconds: number): string {
  return `${PHASE_WORDS[phase ?? "waiting"]}, ${Math.max(0, Math.floor(seconds))} s`;
}

const ABSTAIN_WORDS: Record<string, string> = {
  not_enough_evidence: "There was not enough evidence in what it read to answer.",
  no_data: "There is no data in this project to answer from.",
  outside_what_it_reads: "This is outside what chat can read.",
};

const FAILURE_WORDS: Record<string, string> = {
  bad_key: "The key was refused.",
  limited: "Your AI provider's rate limit was reached. Try again later.",
};
const PROVIDER_DID_NOT_ANSWER = "Your AI provider did not answer.";

/** What a final state says in nexovue's words, or null for one that shows its answer. */
export function outcomeSentence(message: MessageView, resetsOn?: string): string | null {
  switch (message.state) {
    case "answered":
    case "queued":
    case "running":
      return null;
    case "partial":
      return USES_ONLY_READS;
    case "abstained":
      return (
        ABSTAIN_WORDS[message.abstain_reason ?? ""] ?? ABSTAIN_WORDS.not_enough_evidence ?? null
      );
    case "withheld":
      return "The answer could not be checked, so it is not shown.";
    case "stopped":
      return "Stopped.";
    case "refused":
      return "Your access or AI settings changed during this question.";
    case "failed":
      return FAILURE_WORDS[message.failure_reason ?? ""] ?? PROVIDER_DID_NOT_ANSWER;
    case "over_limit":
      return `Chat has paused for this month to keep room for critical finding explanations.${
        resetsOn ? ` Resets ${resetsOn}.` : ""
      } An admin can raise the limit.`;
    case "unavailable":
      return "Not available right now.";
  }
}

/** The states that show the message id as a reference and offer a look and a retry. */
const NEEDS_REFERENCE: readonly MessageState[] = [
  "failed",
  "unavailable",
  "withheld",
  "over_limit",
];

export function offersCheckItYourself(state: MessageState): boolean {
  return NEEDS_REFERENCE.includes(state);
}

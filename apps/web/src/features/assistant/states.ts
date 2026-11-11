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
  unchecked:
    "The model's wording: it read these steps, and this sentence was not checked against them",
};
export const CAUSE_WARNING = "nexovue has not measured a cause";
export const REMOVED_ONE = "One sentence was removed because it cited nothing it had read.";
export const removedMany = (count: number) =>
  `${count} sentences were removed because they cited nothing they had read.`;
export const NOT_VERIFIED = "Not verified";
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
};
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

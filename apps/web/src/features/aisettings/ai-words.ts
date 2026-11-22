import type {
  AISettings,
  CheckReason,
  ConnectionView,
  Missing,
  UnavailableReason,
  Usage,
  WireFormat,
} from "./api";
import type { KeywordOnlyReason } from "./chat-api";

/**
 * Every sentence `/settings/ai` says (F3 design, *AI settings*; spec FR26 to FR30, AC85). Pure,
 * so each can be tested without rendering, and the screen holds layout only.
 */

export const MEMBER_LINE = "AI wording is off here. An owner or admin can set it up.";
export const MEMBER_ON_LINE =
  "AI wording is on here. Each explanation says which words came from a model.";
export const MCP_LINE =
  "This switch does not cover the MCP server: AI assistants reach it with your own read tokens, and this switch does not affect them.";
export const CONTAINER_NOT_RUNNING = "The AI container is not running on this server.";
export const NO_ANSWER =
  "The AI container did not answer. Ask whoever runs this server to check it.";
export const BEAT_LOCK_EXPOSED =
  "This database would not let setup remove a right from the AI account. If the AI container were broken into, it could stop scheduled work. See the operations guide.";
export const OWNER_LEFT =
  "The owner who agreed has left. Their agreement stands, and an owner can agree again.";
export const MODEL_CHANGED = "The model changed. AI is off until a test of the new model passes.";
export const MODEL_CHANGE_NOTE = "AI turns off until this passes a test.";
export const NOT_YET_TESTED_BY_NEXOVUE =
  "Not yet tested by nexovue: a connection may be used once its test passes, and is called supported only after nexovue's injection and fault tests.";
export const NO_FORMAT_YET =
  "No provider format is offered yet: each appears here once nexovue has completed one real call through it and recorded the date.";
export const NO_RUNS = "No runs yet";
export const LIMIT_GUESS =
  "Suggested: 2 million tokens a month, a guess until nexovue measures a real run. Warning findings stop at 80% of the limit; critical findings may use all of it.";
export const GATEWAY_LINE =
  "If this address is a gateway, the model name you choose can decide which company receives the facts.";
// The selling line the founder approved (foundation item 2), without its "own server" clause
// until a local model passes the bed (FR30, A81).
export const SELLING_LINE =
  "Your telemetry is stored on your server. AI is off until you turn it on, and you choose where it runs: your own AI account. Finding explanations send only numbers, times, fixed labels and stand-in names, and you can read the exact request first. Chat, if you add it, sends questions, log text, notes and runbooks under its own consent.";

export const FORMAT_WORDS: Record<WireFormat, string> = {
  openai_chat: "OpenAI-style (OpenAI, gateways, Ollama, vLLM)",
  anthropic_messages: "Claude's Messages API",
  bedrock_converse: "Amazon Bedrock Converse",
};

const UNAVAILABLE_WORDS: Record<UnavailableReason, string> = {
  hosted: "Not offered on the hosted service yet.",
  account_missing: "AI is not available: the database account could not be created.",
  no_key: "AI is not available: AI_SECRET_KEY is not set on this server.",
};

const MISSING_WORDS: Record<Missing, string> = {
  connection: "Save a provider connection",
  container: "Start the AI container on this server",
  test: "Pass a test of the saved connection",
  limit: "Set a monthly token limit",
  consent: "An owner agrees to what is sent",
};

/** Where each missing step is done on this screen. */
export const MISSING_ANCHOR: Record<Missing, string> = {
  connection: "ai-provider",
  container: "ai-status",
  test: "ai-test",
  limit: "ai-provider",
  consent: "ai-consent",
};

const CHECK_WORDS: Record<CheckReason, string> = {
  no_connection: "There is no saved connection to test.",
  not_allowed: "The saved address, model or key is not one nexovue may use.",
  key_unreadable: "The saved key could not be opened. Save the key again.",
  address_refused:
    "The address was refused: it is private and not listed in AI_PRIVATE_ENDPOINTS, or it is loopback.",
  timed_out: "The provider did not answer in time.",
  unreachable: "The provider could not be reached.",
  bad_key: "The provider refused the key.",
  not_found: "The provider does not know that model or address.",
  bad_request: "The provider refused the request as malformed. Check the format and model.",
  limited: "The provider is limiting requests. Try again later.",
  unavailable: "The provider answered with an error.",
  refused: "The provider refused to answer.",
  too_long: "The answer was cut off by its length limit.",
  invalid: "The provider's answer could not be read.",
};

export function unavailableLine(reason: UnavailableReason): string {
  return UNAVAILABLE_WORDS[reason];
}

export function missingLine(missing: Missing): string {
  return MISSING_WORDS[missing];
}

export function checkLine(ok: boolean, reason: CheckReason | null | undefined): string {
  if (ok) return "Passed: the provider answered.";
  return reason ? CHECK_WORDS[reason] : "The test did not pass.";
}

/** The status sentence for owners and admins, in one line. */
export function statusLine(settings: AISettings): string {
  if (settings.on) return "AI wording is on.";
  if (settings.off_reason === "consent_ended")
    return "AI wording is off: a change ended the owners' agreement.";
  return "AI wording is off.";
}

/** FR30: where data goes, in words that differ for a private address. */
export function whereDataGoes(connection: ConnectionView): string {
  if (connection.address_kind === "private")
    return `Facts go to ${connection.provider_host}, a private address on your own network that this server's operator allowed. Whatever runs there decides what it keeps.`;
  return `Facts go to ${connection.provider_host}, your provider. Your key identifies you to that provider, and its terms decide what it keeps.`;
}

/** The address-change warning (design, *Provider*, dangerous state). */
export function eraseWarning(organization: string): string {
  return `This erases the saved key and turns AI off for ${organization} now. An owner must agree again before it can return.`;
}

const MILLION = 1_000_000;
const THOUSAND = 1_000;

/** "1.2 million", "450 thousand", "800": tokens in words a person reads at a glance. */
export function tokens(count: number): string {
  if (count >= MILLION) return `${trim(count / MILLION)} million`;
  if (count >= THOUSAND * 10) return `${trim(count / THOUSAND)} thousand`;
  return String(count);
}

function trim(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

const RESET_DAY = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});

/** FR29: "1.2 million of 2 million tokens used, resets 1 December". */
export function usageLine(usage: Usage): string {
  const resets = RESET_DAY.format(new Date(`${usage.resets_on}T00:00:00Z`));
  if (usage.monthly_token_limit === null)
    return `${tokens(usage.used_tokens)} tokens used this month, with no limit set, resets ${resets}`;
  return `${tokens(usage.used_tokens)} of ${tokens(usage.monthly_token_limit)} tokens used, resets ${resets}`;
}

/** The share of the limit used, 0 to 100, for the bar; none without a limit. */
export function usedShare(usage: Usage): number | null {
  if (!usage.monthly_token_limit) return null;
  return Math.min(100, Math.round((usage.used_tokens / usage.monthly_token_limit) * 100));
}

/** BR14: money is only ever an estimate, from the prices typed. */
export function costLine(usage: Usage): string | null {
  if (usage.estimated_cost === null) return null;
  const worst =
    usage.worst_case_cost === null
      ? ""
      : ` At the limit, at most about ${money(usage.worst_case_cost)}.`;
  return `Estimated cost this month: about ${money(usage.estimated_cost)}, from the prices you entered.${worst}`;
}

function money(value: string): string {
  return Number(value).toFixed(2);
}

export const OUTCOME_WORDS: Record<string, string> = {
  started: "Running",
  done: "Done",
  abstained: "Nothing to add",
  unavailable: "Provider error",
  limited: "Rate limited",
  bad_key: "Key refused",
  refused: "Refused",
  too_long: "Too long",
  invalid: "Unreadable answer",
  died: "Stopped",
  not_found: "Model not found",
  bad_request: "Request refused",
  unknown: "Unknown",
};

export function outcomeLine(outcome: string): string {
  return OUTCOME_WORDS[outcome] ?? OUTCOME_WORDS.unknown;
}

// ---------------------------------------------------------------- F4: chat (T2.12)

/** BR18's sentence, word for word (spec; AC99): what chat can and cannot do, on the consent
 *  step and the setup page alike. `test_banned_claims.py` holds the page to the same words. */
export const CHAT_LIMITS_LINE =
  "Chat has no tool that can write. nexovue checks each answer and removes model-written links and commands; that check can miss things. Log text goes to the AI provider you choose.";
export const CHAT_MCP_LINE =
  "This switch does not affect the MCP server. Your own AI tools reach it with API tokens your team made.";
export const CHAT_MASKING_LINE =
  "Masking catches some credentials: Bearer and Basic values, card numbers, passwords in URLs, and attributes with credential-like names. A password written into a log line's text may not be caught. Names and customer ids stay, and are sent.";
export const CHAT_NO_READS = "Chat has read nothing yet.";
export const CHAT_DATA_LEVEL_WORDS: Record<"facts" | "facts_and_text", string> = {
  facts: "Facts only: findings and services, never log text",
  facts_and_text: "Facts and text: also log lines, which anyone who can reach your service writes",
};
export const CHAT_COUNT_WORDS: Record<string, string> = {
  answered: "Answered",
  partial: "Partial",
  abstained: "Not enough evidence",
  withheld: "Withheld by the checks",
  failed: "Failed",
  unavailable: "Not available",
  over_limit: "Over the limit",
  refused: "Refused",
  stopped: "Stopped",
};
/** Share of chat's own ceiling at which the section warns (FR19; design, a guess). */
export const CHAT_WARNING_SHARE = 0.8;
/** Chat's ceiling: the share of the month's limit questions may reach (F3's `CHAT_SHARE`). */
export const CHAT_SHARE = 0.8;

export function maskingOffLine(rules: string[]): string {
  return rules.length === 0
    ? "This project has every default masking rule on."
    : `This project has these masking rules off: ${rules.join(", ")}.`;
}

export function chatNearCeiling(used: number, limit: number | null): boolean {
  return limit !== null && limit > 0 && used >= limit * CHAT_SHARE * CHAT_WARNING_SHARE;
}

export function countWord(kind: string): string {
  if (kind.startsWith("rejected_"))
    return `Rejected: ${kind.slice("rejected_".length).replaceAll("_", " ")}`;
  if (kind.startsWith("wrong_"))
    return `Marked wrong: ${kind.slice("wrong_".length).replaceAll("_", " ")}`;
  return CHAT_COUNT_WORDS[kind] ?? kind.replaceAll("_", " ");
}

// ---------------------------------------------------------------- chat's meaning search (F4 Step 6)

/** Why chat searches by keyword only, said in the Chat section (FR27, AC43). */
export const KEYWORD_ONLY_WORDS: Record<KeywordOnlyReason, string> = {
  no_extension:
    "Search is keyword only: this server's database has no vector extension. Whoever runs it can create the extension, then run setup again.",
  no_model: "Search is keyword only: no embedding model is set.",
  cannot_embed:
    "Search is keyword only: this provider has no embedding model. Add a connection for embeddings only.",
  no_consent:
    "Search is keyword only: an owner has not agreed to send text to the connection for embeddings.",
  not_tested: "Search is keyword only until a check of this embedding model passes.",
  chat_not_ready:
    "Search is keyword only: chat must be on at the facts and text level, with an owner's agreement.",
};
export const MEANING_SEARCH_ON =
  "Search finds runbooks and fact lines by meaning as well as by keyword.";
export const EMBEDDING_SENDS =
  "With a model set, every runbook piece, case note and fact line is sent once to be embedded, and each search phrase too. Masking applies first.";

/** An embedding check's failure, in the connection test's words, with one of its own. */
export function embeddingCheckLine(reason: string | null): string {
  if (reason === "no_embeddings") return "This provider has no embedding model.";
  if (reason !== null && reason in CHECK_WORDS) return CHECK_WORDS[reason as CheckReason];
  return "The check did not pass.";
}

export function embeddingProgress(embedded: number, total: number): string {
  if (total === 0) return "No runbooks, notes or fact lines to embed yet.";
  return `${embedded} of ${total} runbook pieces, notes and fact lines can be found by meaning.`;
}

/**
 * F4 FR32, AC83: notes chat cannot search. A note always saves; the index adds it within a few
 * minutes, and skips it while its project already holds 5,000 pieces. Null when none.
 */
export function notesNotSearchable(count: number): string | null {
  if (count <= 0) return null;
  const notes = count === 1 ? "1 note is" : `${count} notes are`;
  return `${notes} not searchable by chat. New notes are added within a few minutes; a project holding 5,000 pieces of runbooks and notes takes no more.`;
}

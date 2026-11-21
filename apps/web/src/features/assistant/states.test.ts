import { describe, expect, it } from "vitest";

import { message } from "./panel.test-util";
import {
  agedOut,
  LOST_CONTACT,
  NO_ROWS,
  noKnowledgeRecord,
  outcomeSentence,
  QUOTE_GONE,
  QUOTE_READ_FAILED,
  READ_REFUSED_FOR_NOW,
  RUNBOOK_QUOTE_CUT,
  runbookQuoteLabel,
  STANDING_WORDS,
  teamLabel,
} from "./states";

/**
 * F4 T3.5 (AC30): the sentence each *Error cases* row of the spec says, written here as the
 * spec writes it, so a change to the module's words fails rather than passing with them.
 * Each row's state is produced by the tests `apps/api/tests/test_assistant_error_cases.py`
 * lists; the panel's render tests show these words in place.
 */

describe("each error case's sentence, word for word", () => {
  it.each([
    ["AI off, member", STANDING_WORDS.ai_off(false), "Ask an admin to turn this on"],
    ["Project opted out", STANDING_WORDS.opted_out(false), "AI is off for this project"],
    ["No telemetry yet", STANDING_WORDS.no_data(false), "sent no telemetry yet"],
    [
      "AI container stopped",
      outcomeSentence(message({ state: "unavailable" })),
      "Not available right now",
    ],
    [
      "Provider down",
      outcomeSentence(message({ state: "failed", failure_reason: "unreachable" })),
      "Your AI provider did not answer",
    ],
    [
      "Bad key",
      outcomeSentence(message({ state: "failed", failure_reason: "bad_key" })),
      "The key was refused",
    ],
    [
      "Provider rate limit",
      outcomeSentence(message({ state: "failed", failure_reason: "limited" })),
      "rate limit",
    ],
    [
      "Chat's share gone",
      outcomeSentence(message({ state: "over_limit" }), "1 December"),
      "Resets 1 December. An admin can raise the limit.",
    ],
    [
      "Removed mid-question",
      outcomeSentence(message({ state: "refused" })),
      "Your access or AI settings changed during this question",
    ],
    ["Tool refused by rate limit", READ_REFUSED_FOR_NOW, "the read was refused for now"],
    ["Data aged out", agedOut("2026-11-05"), "The data before 2026-11-05 is no longer kept"],
    ["No rows", NO_ROWS, "No matching rows in what was stored"],
    ["Quote's log aged out", QUOTE_GONE.search_logs, "This log line is no longer available."],
    ["Quote's read failed", QUOTE_READ_FAILED, "Could not read this now"],
    ["Panel poll failing", LOST_CONTACT, "Lost contact; this question may still be running"],
    [
      "Knowledge has no match",
      noKnowledgeRecord(30),
      "No record of this in what is kept. Cases are kept for 30 days after they end",
    ],
  ] as const)("%s", (_, said, spec) => {
    expect(said?.toLowerCase()).toContain(spec.toLowerCase());
  });
});

describe("the design's words for the team's own text (F4 *Words*)", () => {
  it.each([
    ["Notes label", teamLabel("12 days old"), "From your team's notes, 12 days old"],
    [
      "Runbook quote",
      runbookQuoteLabel("12 days old"),
      "From a runbook your team wrote, 12 days old",
    ],
    [
      "Runbook quote cut",
      RUNBOOK_QUOTE_CUT,
      "Cut at 500 characters. Open the runbook for all of it",
    ],
  ] as const)("%s", (_, said, design) => {
    expect(said).toBe(design);
  });
});

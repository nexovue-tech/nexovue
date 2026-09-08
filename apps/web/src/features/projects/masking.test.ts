import { describe, expect, it } from "vitest";

import { MASKING_TEXT_KEYS, type MaskingDocument } from "./api";
import { documentOf, formOf, newlyDisabled } from "./masking";

describe("the masking document round trip", () => {
  /**
   * The one that matters. `PUT /masking-config` replaces the stored document, so
   * a form that reads some keys and writes the rest away deletes them silently —
   * the response is a 200 carrying the damage.
   */
  it("carries every key back out that it read in", () => {
    const stored: MaskingDocument = {
      disable: ["cookies"],
      hash: ["card_numbers"],
      mask_keys: ["session_token"],
      mask_key_contains: ["secret"],
      hash_keys: ["user.email"],
      hash_key_contains: ["ip"],
      allow_values: ["anonymous"],
    };

    expect(documentOf(formOf(stored))).toEqual(stored);
  });

  it("keeps a text key the form never touched", () => {
    const stored: MaskingDocument = { allow_values: ["anonymous"] };

    const form = formOf(stored);
    form.disabled.add("cookies");

    expect(documentOf(form).allow_values).toEqual(["anonymous"]);
  });

  it("omits an empty key rather than sending an empty list", () => {
    // Both say the same thing to the API. An absent key keeps the stored
    // document readable to whoever opens it next.
    const document = documentOf(formOf({}));

    expect(document).toEqual({});
    for (const key of MASKING_TEXT_KEYS) expect(document[key]).toBeUndefined();
  });

  it("trims each entry and drops blank lines", () => {
    const form = formOf({});
    form.text.mask_keys = "  session_token \n\n  api_key\n   \n";

    expect(documentOf(form).mask_keys).toEqual(["session_token", "api_key"]);
  });
});

describe("the rules a save would newly switch off", () => {
  /**
   * The API refuses the write unless each newly disabled rule is named, and
   * records an audit row for it. Computed here first so the customer is asked
   * before the server refuses.
   */
  it("names a rule being switched off for the first time", () => {
    expect(newlyDisabled({}, { disable: ["cookies"] })).toEqual(["cookies"]);
  });

  /**
   * A prompt that appeared on every save is a prompt people click through, which
   * would defeat the one confirmation that guards what leaves the process.
   */
  it("says nothing about a rule that was already off", () => {
    const stored: MaskingDocument = { disable: ["cookies"] };

    expect(newlyDisabled(stored, { disable: ["cookies"] })).toEqual([]);
  });

  it("names only the addition when a rule is switched off beside an existing one", () => {
    const stored: MaskingDocument = { disable: ["cookies"] };

    expect(newlyDisabled(stored, { disable: ["cookies", "card_numbers"] })).toEqual([
      "card_numbers",
    ]);
  });

  it("says nothing when a rule is switched back on", () => {
    const stored: MaskingDocument = { disable: ["cookies"] };

    expect(newlyDisabled(stored, {})).toEqual([]);
  });
});

import { MASKING_TEXT_KEYS, type MaskingDocument, type MaskingTextKey } from "./api";

/**
 * The form's own shape, and the two conversions either side of it.
 *
 * These are functions rather than inline JSX because they carry the rule the
 * screen would otherwise get wrong silently: **the masking endpoint replaces the
 * whole document.** A form that reads five keys and writes five keys deletes the
 * other two, and nothing about the response would say so.
 */
export interface MaskingForm {
  disabled: Set<string>;
  hashed: Set<string>;
  /** One entry per line, kept as the customer typed it until it is saved. */
  text: Record<MaskingTextKey, string>;
}

const EMPTY_TEXT = Object.fromEntries(MASKING_TEXT_KEYS.map((key) => [key, ""])) as Record<
  MaskingTextKey,
  string
>;

export function formOf(document: MaskingDocument): MaskingForm {
  return {
    disabled: new Set(document.disable ?? []),
    hashed: new Set(document.hash ?? []),
    text: {
      ...EMPTY_TEXT,
      ...Object.fromEntries(
        MASKING_TEXT_KEYS.map((key) => [key, (document[key] ?? []).join("\n")]),
      ),
    },
  };
}

/**
 * Blank lines are dropped and each entry is trimmed, matching what the worker
 * does to them anyway. Doing it here means the customer sees the stored document
 * come back looking like what they typed rather than subtly different.
 *
 * A key with no entries is omitted rather than sent as `[]`. Both mean the same
 * thing to the API, and an absent key keeps the stored document readable.
 */
export function documentOf(form: MaskingForm): MaskingDocument {
  const document: MaskingDocument = {};
  if (form.disabled.size > 0) document.disable = [...form.disabled].sort();
  if (form.hashed.size > 0) document.hash = [...form.hashed].sort();

  for (const key of MASKING_TEXT_KEYS) {
    const entries = form.text[key]
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    if (entries.length > 0) document[key] = entries;
  }
  return document;
}

/**
 * The rules this save would switch off that were not off already.
 *
 * The API refuses the write unless every one of them is named in
 * `confirm_disabling`, and it writes an audit row for each. Computed the same
 * way here so the screen can ask before the server refuses — the difference of
 * two sets, never the whole `disable` list, because re-confirming a rule that
 * was already off would teach a customer to click through the one prompt that
 * matters.
 */
export function newlyDisabled(stored: MaskingDocument, next: MaskingDocument): string[] {
  const alreadyOff = new Set(stored.disable ?? []);
  return (next.disable ?? []).filter((rule) => !alreadyOff.has(rule)).sort();
}

/** The rule names in prose, for a prompt that says what is being turned off. */
export const RULE_LABELS: Record<string, string> = {
  credential_keys: "Credential keys",
  authorization_headers: "Authorization headers",
  cookies: "Cookies",
  card_numbers: "Card numbers",
  authorization_credentials: "Authorization credentials",
  url_credentials: "Passwords and signatures inside URLs",
};

export const TEXT_KEY_LABELS: Record<MaskingTextKey, { label: string; hint: string }> = {
  mask_keys: {
    label: "Mask these attribute keys",
    hint: "An exact attribute key. Its value is replaced with a placeholder.",
  },
  mask_key_contains: {
    label: "Mask keys containing",
    hint: "A fragment. Any attribute key containing it is masked.",
  },
  hash_keys: {
    label: "Hash these attribute keys",
    hint: "The value becomes a stable pseudonym, so you can still group by it.",
  },
  hash_key_contains: {
    label: "Hash keys containing",
    hint: "A fragment, hashed rather than blanked.",
  },
  allow_values: {
    label: "Never mask these values",
    hint: "An exact value that should survive every rule above.",
  },
};

/**
 * Whether two masking documents say the same thing.
 *
 * Needed because the form and the stored document are not comparable directly: the form
 * holds sets and free text, and `documentOf` sorts, trims and drops empty keys on the way
 * out. Comparing the round-tripped document against the stored one is what makes "typed
 * and undone" register as no change — which is the whole difference between a warning
 * people read and one they learn to click away.
 *
 * Order-insensitive for the same reason: a stored `["b", "a"]` and a typed `["a", "b"]`
 * are the same rules, and warning about them would be warning about nothing.
 */
export function sameMasking(left: MaskingDocument, right: MaskingDocument): boolean {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);

  for (const key of keys) {
    const ours = [...(left[key as keyof MaskingDocument] ?? [])].sort();
    const theirs = [...(right[key as keyof MaskingDocument] ?? [])].sort();
    if (ours.length !== theirs.length) return false;
    if (ours.some((entry, index) => entry !== theirs[index])) return false;
  }
  return true;
}

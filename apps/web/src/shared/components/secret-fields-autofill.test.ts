import { describe, expect, it } from "vitest";

/**
 * A key, token or secret field is not a login. Browsers ignore `autocomplete="off"` beside a
 * password field and fill the person's saved login into it and the text field before it:
 * the AI connection's region was saved as the signed-in person's email this way (UX review,
 * 2026-11-28). `new-password` is the value browsers honour for "not your login here".
 *
 * Structural: it reads every `type="password"` input, as `unsaved-changes-coverage.test.ts`
 * reads forms. Login, password change and re-confirmation fields are the exceptions, by file.
 */

const SOURCES: Record<string, string> = import.meta.glob("/src/**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
});

/** Where a password field *is* the person's own password. */
const LOGIN_FILES = new Set([
  "/src/features/auth/LoginScreen.tsx",
  "/src/features/auth/SignupScreen.tsx",
  "/src/features/auth/AcceptInviteScreen.tsx",
  "/src/features/auth/ResetPasswordScreen.tsx",
  "/src/features/auth/account-sections.tsx",
  "/src/features/admin/ConfirmItIsYou.tsx",
]);

/** The elements a secret field is written with: the native input, or the shared field. */
const FIELD_TAGS = ["<input", "<TextField"];

/** Each password field's own text, from its tag to its closing `/>`. */
function passwordInputs(text: string): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(/type="password"/g)) {
    const start = Math.max(...FIELD_TAGS.map((tag) => text.lastIndexOf(tag, match.index)));
    const end = text.indexOf("/>", match.index);
    if (start !== -1 && end !== -1) found.push(text.slice(start, end));
  }
  return found;
}

describe("secret fields", () => {
  it("every key, token or secret field tells the browser it is not a login", () => {
    const wrong: string[] = [];
    for (const [path, text] of Object.entries(SOURCES)) {
      if (LOGIN_FILES.has(path) || path.endsWith(".test.tsx")) continue;
      for (const input of passwordInputs(text)) {
        if (!input.includes('autoComplete="new-password"'))
          wrong.push(`${path}: ${input.slice(0, 60)}`);
      }
    }
    expect(wrong).toEqual([]);
  });

  it("finds the fields it guards", () => {
    const guarded = Object.entries(SOURCES).filter(
      ([path, text]) => !LOGIN_FILES.has(path) && passwordInputs(text).length > 0,
    );
    expect(guarded.length).toBeGreaterThanOrEqual(4);
  });
});

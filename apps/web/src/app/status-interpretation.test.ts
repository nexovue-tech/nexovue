/**
 * The HTTP status is interpreted in one place, as a property of the source.
 *
 * Every other test here asks "does today's code behave?". This one asks "can
 * tomorrow's misbehave?" — it reads `src/` and fails on the *shape* of a change,
 * so a screen that decides for itself what a 401 means is named by this suite
 * rather than discovered when a session silently stops expiring.
 *
 * `app/query-client.ts` already says why: a 401 clears the session, a 403 is the
 * route boundary's, a 429 honours Retry-After, a 5xx retries with backoff. A
 * feature that reimplements one of those has two answers to the same question,
 * and only one of them runs.
 */

import { describe, expect, it } from "vitest";

/**
 * Every source file under `src/`, read as text.
 *
 * Vite's own glob rather than `node:fs`: this package compiles without node
 * types, and a structural test that needed them would have to live somewhere
 * other than beside the code it guards.
 */
const SOURCES: Record<string, string> = import.meta.glob("/src/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
});

/** `/src/features/auth/problem-fields.ts` → `features/auth/problem-fields.ts`. */
const within = (path: string) => path.replace(/^\/src\//, "");

/**
 * Where an HTTP status may legitimately be read.
 *
 * `app/query-client.ts` is the interpretation point AC21 names.
 *
 * `shared/api-client.ts` is what *produces* the status: it is the layer that
 * turns a response into an `ApiError`, so it necessarily touches one.
 *
 * `features/auth/problem-fields.ts` is the deliberate second reader, and the
 * exception is narrow on purpose. A 401 from the sign-in form is a wrong
 * password rather than an expired session — `query-client.ts` excludes those
 * screens for that reason — and 409 and 422 are the form's own answers about the
 * fields a person just typed. Moving them into the query client would give one
 * function two unrelated jobs. Adding a fourth entry here is a decision, not a
 * formality: it means a second place now decides what a status means.
 *
 * `admin/api.ts` is a different app's (F31, `admin.html`): staff, their own API and
 * their own cookie, with no query client in common. It both produces its errors
 * and answers the one question the admin app asks of a status — is anyone signed in.
 */
const MAY_READ_A_STATUS: readonly string[] = [
  "app/query-client.ts",
  "shared/api-client.ts",
  "features/auth/problem-fields.ts",
  "admin/api.ts",
];

/** `error.status === 401`, `status >= 500`, `response.status !== 204`. */
const COMPARES_A_STATUS = /\bstatus\s*(?:===|!==|==|!=|>=|<=|>|<)\s*(?:\d{3}\b|[A-Z_]*HTTP[A-Z_]*)/;

/** A bare status code written where a status is being tested. */
const NAMES_A_STATUS_CODE = /\b(?:HTTP_[A-Z_]+|status)\b[^\n]*\b[45]\d{2}\b/;

/** The files this suite checks: everything but its own kind. */
function sourceFiles(): string[] {
  return Object.keys(SOURCES)
    .map(within)
    .filter((path) => !/\.test\.tsx?$/.test(path))
    .sort();
}

function offenders(): string[] {
  const named: string[] = [];
  for (const path of sourceFiles()) {
    if (MAY_READ_A_STATUS.includes(path)) continue;
    const text = SOURCES[`/src/${path}`];
    if (COMPARES_A_STATUS.test(text) || NAMES_A_STATUS_CODE.test(text)) named.push(path);
  }
  return named;
}

describe("interpreting an HTTP status", () => {
  it("happens only where it is allowed to", () => {
    expect(offenders()).toEqual([]);
  });

  it("is something this test can actually see", () => {
    // Without this the suite passes when the pattern stops matching anything at
    // all — the shape of a green test that has quietly stopped reading.
    const allowed = SOURCES["/src/features/auth/problem-fields.ts"];

    expect(allowed).toBeTypeOf("string");
    expect(COMPARES_A_STATUS.test(allowed)).toBe(true);
  });

  it("reads the files it claims to read", () => {
    const files = sourceFiles();

    expect(files).toContain("app/query-client.ts");
    expect(files.length).toBeGreaterThan(100);
    // A test file would trip every pattern it exists to check.
    expect(files.filter((path) => path.includes(".test."))).toEqual([]);
  });

  it("names every file on the allowlist as one that exists", () => {
    // An entry left behind after a rename would silently widen the exception.
    const files = sourceFiles();

    for (const allowed of MAY_READ_A_STATUS) expect(files).toContain(allowed);
  });
});

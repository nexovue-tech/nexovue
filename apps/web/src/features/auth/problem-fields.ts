import { ApiError } from "@/shared/api-client";

/**
 * Turns one API failure into the two things a form can show: a message above the
 * fields, and a message under one of them.
 *
 * The field list is the `errors` extension our problem handler adds to a 422
 * (`{ field, reason }`, field being a Pydantic location like `body.email`). Anything
 * without a field lands on the form.
 *
 * 401, 429 and 5xx are absent on purpose — app/query-client.ts owns those. What
 * is left here is the part only the form can explain.
 *
 * **403 was absent too, and should not have been.** The query client suppresses
 * its toast on the reasoning that "a refusal is a property of the screen, the
 * route boundary renders it" — but the boundary only ever sees a *query*;
 * `throwOnError` is a query option. A refused **write** therefore fell through
 * every layer and said nothing at all: during a support view, pressing Save on
 * this form left the name in the box, no message, and a `PATCH /profile 403
 * "Support view is read-only."` in the API log that only we could see. Found by
 * hand on 2026-09-21 (`docs/changes/console-review-fixes/`).
 */

const HTTP_UNAUTHORIZED = 401;

/** A 401 with no problem body. Rare enough to be a proxy, not the API. */
const UNEXPLAINED_REFUSAL = "Those details were not accepted. Check them and try again.";
const HTTP_FORBIDDEN = 403;
const HTTP_CONFLICT = 409;
const HTTP_UNPROCESSABLE = 422;

/** Pydantic reports `body.email`; the form knows the field as `email`. */
const LOCATION_PREFIX = /^(body|query|path)\./;

export interface FormProblem {
  /** Shown above the fields. Absent when every problem belongs to a field. */
  message?: string;
  fieldErrors: Record<string, string>;
}

const NO_PROBLEM: FormProblem = { fieldErrors: {} };

interface ValidationProblem {
  field: string;
  reason: string;
}

function isValidationProblem(value: unknown): value is ValidationProblem {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.field === "string" && typeof candidate.reason === "string";
}

function fieldErrorsFrom(error: ApiError): Record<string, string> {
  const reported = error.problem?.errors;
  if (!Array.isArray(reported)) return {};

  const byField: Record<string, string> = {};
  for (const item of reported) {
    if (!isValidationProblem(item)) continue;
    const field = item.field.replace(LOCATION_PREFIX, "");
    if (field && !byField[field]) byField[field] = item.reason;
  }
  return byField;
}

/** Shown when the API refused a link without explaining — a 5xx, not a spent token. */
const LINK_REFUSED_WITHOUT_REASON = "That link could not be checked just now. Try again shortly.";

/**
 * Why a recovery link did not work, in the API's own words wherever it gave them.
 *
 * The API answers every way a link can fail — never issued, already spent, expired,
 * minted for the other purpose — with one message on purpose: telling them apart would
 * tell an attacker which guesses were once real. So this passes that message straight
 * through and never tries to narrow it.
 */
export function describeLinkRefusal(error: unknown): string {
  return describeFormProblem(error).message ?? LINK_REFUSED_WITHOUT_REASON;
}

export function describeFormProblem(error: unknown): FormProblem {
  if (!(error instanceof ApiError)) return NO_PROBLEM;

  if (error.context.offline || error.context.aborted) {
    return {
      message: "The request never reached us. Check the connection and try again.",
      fieldErrors: {},
    };
  }

  if (error.status === HTTP_UNAUTHORIZED) {
    // **The API's own sentence, not one written here.** It already answers an unknown
    // email and a wrong password identically, and it answers every way a second factor
    // can fail identically too — a screen that restated either would be a second copy
    // of a rule whose whole value is that it does not vary. The fallback below is for
    // a 401 that arrived without a problem body at all.
    return { message: error.problem?.detail ?? UNEXPLAINED_REFUSAL, fieldErrors: {} };
  }

  if (error.status === HTTP_FORBIDDEN) {
    // The API's own sentence, for the reason the 401 above gives: it already
    // says why in words a customer can act on — "Support view is read-only",
    // "Only an owner can change this" — and a second copy here would drift.
    return { message: error.problem?.detail ?? "You may not do that.", fieldErrors: {} };
  }

  if (error.status === HTTP_CONFLICT) {
    return { fieldErrors: { email: "An account already uses this email." } };
  }

  if (error.status === HTTP_UNPROCESSABLE) {
    const fieldErrors = fieldErrorsFrom(error);
    return Object.keys(fieldErrors).length > 0
      ? { fieldErrors }
      : {
          message: error.problem?.detail ?? "Some of these details are not valid.",
          fieldErrors: {},
        };
  }

  return NO_PROBLEM;
}

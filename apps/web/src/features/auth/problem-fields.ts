import { ApiError } from "@/shared/api-client";

/**
 * Turns one API failure into the two things a form can show: a message above the
 * fields, and a message under one of them.
 *
 * The field list is the `errors` extension our problem handler adds to a 422
 * (`{ field, reason }`, field being a Pydantic location like `body.email`). Anything
 * without a field lands on the form.
 *
 * 401, 403, 429 and 5xx are absent on purpose — app/query-client.ts owns those. What
 * is left here is the part only the form can explain.
 */

const HTTP_UNAUTHORIZED = 401;
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
    // Deliberately one message for both unknown email and wrong password.
    return { message: "That email and password do not match an account.", fieldErrors: {} };
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

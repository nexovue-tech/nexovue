import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import { describeFormProblem } from "./problem-fields";

/**
 * The defect this guards (`docs/changes/console-review-fixes/`): a refused
 * **write** said nothing anywhere. The query client suppresses the 403 toast
 * because "the route boundary renders it" — and the boundary only ever sees a
 * query, because `throwOnError` is a query option. Pressing Save during a
 * support view left the typed name in the box, no message on screen, and a
 * `PATCH /profile 403 "Support view is read-only."` visible only in the API log.
 */

const refused = (status: number, detail?: string) =>
  new ApiError(
    "refused",
    status,
    { traceId: "t", correlationId: "c", offline: false, aborted: false },
    detail === undefined ? undefined : { type: "about:blank", title: "Refused", status, detail },
  );

describe("a refusal a form must explain itself", () => {
  it("says what the API said, in the API's words", () => {
    const problem = describeFormProblem(refused(403, "Support view is read-only."));

    expect(problem.message).toBe("Support view is read-only.");
    expect(problem.fieldErrors).toEqual({});
  });

  it("still says something when the refusal carried no sentence", () => {
    expect(describeFormProblem(refused(403)).message).toBe("You may not do that.");
  });

  it("leaves a 422's field errors to the fields", () => {
    // The 403 branch must not swallow the case this file was written for.
    const invalid = new ApiError(
      "invalid",
      422,
      { traceId: "t", correlationId: "c", offline: false, aborted: false },
      {
        type: "about:blank",
        title: "Invalid",
        status: 422,
        errors: [{ field: "body.email", reason: "not an address" }],
      },
    );

    expect(describeFormProblem(invalid).fieldErrors).toEqual({ email: "not an address" });
  });
});

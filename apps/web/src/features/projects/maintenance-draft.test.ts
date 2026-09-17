import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";
import { toLocalInput } from "@/shared/local-instant";

import type { CheckChoice, MaintenanceWindow } from "./check-settings-api";
import type { MaintenanceDraft } from "./maintenance-draft";
import {
  describeRecurrence,
  describeScope,
  draftOf,
  draftProblems,
  emptyDraft,
  requestOf,
  sameWindow,
  serverProblems,
} from "./maintenance-draft";

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100";
const CHECK_A = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0a01";
const CHECK_B = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0a02";

const STORED: MaintenanceWindow = {
  id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0200",
  project_id: PROJECT_ID,
  name: "Database upgrade",
  starts_at: "2026-09-20T02:00:00.000Z",
  ends_at: "2026-09-20T04:00:00.000Z",
  recurrence: "weekly",
  repeat_until: "2026-12-20T02:00:00.000Z",
  applies_to_all: false,
  check_ids: [CHECK_A, CHECK_B],
  active_now: false,
  created_at: "2026-09-15T10:00:00.000Z",
};

/** A draft that passes every rule, in the zone the test machine happens to be in. */
function validDraft(overrides: Partial<MaintenanceDraft> = {}): MaintenanceDraft {
  return {
    ...emptyDraft(),
    name: "Deploy",
    startsAt: toLocalInput("2026-09-20T02:00:00.000Z"),
    endsAt: toLocalInput("2026-09-20T03:00:00.000Z"),
    ...overrides,
  };
}

describe("a draft and the request it becomes", () => {
  it("round-trips a stored window to the same instants, whatever the machine's zone", () => {
    const request = requestOf(draftOf(STORED), PROJECT_ID);

    expect(request).toEqual({
      project_id: PROJECT_ID,
      name: "Database upgrade",
      starts_at: STORED.starts_at,
      ends_at: STORED.ends_at,
      recurrence: "weekly",
      repeat_until: STORED.repeat_until,
      applies_to_all: false,
      check_ids: [CHECK_A, CHECK_B],
    });
  });

  it("sends instants with an offset, which is what the API requires", () => {
    const request = requestOf(validDraft(), PROJECT_ID);

    expect(request?.starts_at).toMatch(/Z$/);
    expect(request?.ends_at).toMatch(/Z$/);
  });

  it("sends no end date for a window that does not repeat, even one typed before switching", () => {
    const request = requestOf(
      validDraft({ recurrence: "none", repeatUntil: toLocalInput(STORED.ends_at) }),
      PROJECT_ID,
    );

    expect(request?.repeat_until).toBeNull();
  });

  it("names no checks for a project-wide window, even ones ticked before switching", () => {
    const request = requestOf(validDraft({ appliesToAll: true, checkIds: [CHECK_A] }), PROJECT_ID);

    expect(request?.applies_to_all).toBe(true);
    expect(request?.check_ids).toEqual([]);
  });

  it("trims the name", () => {
    expect(requestOf(validDraft({ name: "  Deploy  " }), PROJECT_ID)?.name).toBe("Deploy");
  });

  it("returns nothing for a time it cannot read rather than an instant in 1970", () => {
    expect(requestOf(validDraft({ startsAt: "" }), PROJECT_ID)).toBeUndefined();
  });
});

describe("the rules a draft must pass", () => {
  it("passes a complete one-off window over the whole project", () => {
    expect(draftProblems(validDraft())).toEqual({});
  });

  it("asks for a name and both times", () => {
    const problems = draftProblems(emptyDraft());

    expect(problems.name).toBe("Name the window.");
    expect(problems.starts_at).toBe("Choose when it starts.");
    expect(problems.ends_at).toBe("Choose when it ends.");
  });

  it("refuses an end at or before the start", () => {
    const start = toLocalInput("2026-09-20T02:00:00.000Z");

    expect(draftProblems(validDraft({ startsAt: start, endsAt: start })).ends_at).toBe(
      "Must be after the start.",
    );
    expect(
      draftProblems(validDraft({ endsAt: toLocalInput("2026-09-20T01:00:00.000Z") })).ends_at,
    ).toBe("Must be after the start.");
  });

  it("refuses a daily window a day long, which would run into its own next occurrence", () => {
    const problems = draftProblems(
      validDraft({ recurrence: "daily", endsAt: toLocalInput("2026-09-21T02:00:00.000Z") }),
    );

    expect(problems.ends_at).toContain("shorter than one day");
  });

  it("allows the same two days once it repeats weekly instead", () => {
    const draft = validDraft({
      recurrence: "weekly",
      endsAt: toLocalInput("2026-09-22T02:00:00.000Z"),
    });

    expect(draftProblems(draft)).toEqual({});
  });

  it("refuses a weekly window a week long", () => {
    const problems = draftProblems(
      validDraft({ recurrence: "weekly", endsAt: toLocalInput("2026-09-27T02:00:00.000Z") }),
    );

    expect(problems.ends_at).toContain("shorter than one week");
  });

  it("refuses a repeat end before the first start, which would never take effect", () => {
    const problems = draftProblems(
      validDraft({ recurrence: "daily", repeatUntil: toLocalInput("2026-09-19T02:00:00.000Z") }),
    );

    expect(problems.repeat_until).toBe("Must not be before the first start.");
  });

  it("ignores a repeat end on a window that does not repeat, because the form hides it", () => {
    const problems = draftProblems(
      validDraft({ recurrence: "none", repeatUntil: toLocalInput("2026-09-19T02:00:00.000Z") }),
    );

    expect(problems.repeat_until).toBeUndefined();
  });

  it("asks for checks, or the whole project", () => {
    const problems = draftProblems(validDraft({ appliesToAll: false, checkIds: [] }));

    expect(problems.check_ids).toBe("Choose at least one check, or cover the whole project.");
  });
});

describe("whether a draft is an unsaved change", () => {
  it("is not, straight after opening a stored window", () => {
    expect(sameWindow(draftOf(STORED), STORED)).toBe(true);
  });

  it("is, once a field differs", () => {
    expect(sameWindow({ ...draftOf(STORED), name: "Something else" }, STORED)).toBe(false);
  });

  it("is not, for checks ticked in another order", () => {
    expect(sameWindow({ ...draftOf(STORED), checkIds: [CHECK_B, CHECK_A] }, STORED)).toBe(true);
  });

  it("is not, for a name typed and undone with a trailing space", () => {
    expect(sameWindow({ ...draftOf(STORED), name: "Database upgrade " }, STORED)).toBe(true);
  });

  it("compares a new window against the blank form", () => {
    expect(sameWindow(emptyDraft(), undefined)).toBe(true);
    expect(sameWindow({ ...emptyDraft(), name: "D" }, undefined)).toBe(false);
  });
});

describe("the API's own refusals", () => {
  const context = { traceId: "t", correlationId: "c", offline: false, aborted: false };
  const refused = (errors: unknown) =>
    new ApiError("Unprocessable", 422, context, {
      type: "about:blank",
      title: "Unprocessable",
      status: 422,
      detail: "This maintenance window cannot be saved.",
      errors,
    });

  it("places each reason under the field it names, as a sentence", () => {
    const problems = serverProblems(
      refused([{ field: "ends_at", reason: "a daily window must be shorter than its period" }]),
    );

    expect(problems).toEqual({ ends_at: "A daily window must be shorter than its period." });
  });

  it("keeps every reason for one field, and strips Pydantic's location", () => {
    const problems = serverProblems(
      refused([
        { field: "body.check_ids", reason: `no check ${CHECK_A} in this project` },
        { field: "check_ids", reason: `no check ${CHECK_B} in this project` },
      ]),
    );

    expect(problems.check_ids).toContain(CHECK_A);
    expect(problems.check_ids).toContain(CHECK_B);
  });

  it("has nothing to place for a refusal without field errors", () => {
    expect(serverProblems(refused(undefined))).toEqual({});
    expect(serverProblems(new Error("offline"))).toEqual({});
  });
});

describe("reading a window back", () => {
  const checks: CheckChoice[] = [
    { id: CHECK_A, name: "Checkout API", type: "api" },
    { id: CHECK_B, name: "Login flow", type: "multistep" },
  ];

  it("says whether and how it repeats", () => {
    expect(describeRecurrence("none", undefined)).toBe("Does not repeat");
    expect(describeRecurrence("daily", undefined)).toBe("Repeats every day, with no end date");
    expect(describeRecurrence("weekly", "20 Dec 2026")).toBe(
      "Repeats every week until 20 Dec 2026",
    );
  });

  it("says a project-wide window covers every check, later ones included", () => {
    expect(describeScope({ applies_to_all: true, check_ids: [] }, checks)).toBe(
      "Every check in this project, including checks added later",
    );
  });

  it("names the chosen checks", () => {
    expect(describeScope(STORED, checks)).toBe("2 checks: Checkout API, Login flow");
  });

  it("counts the checks while their names are unknown", () => {
    expect(describeScope(STORED, undefined)).toBe("2 checks");
    expect(describeScope(STORED, checks.slice(0, 1))).toBe("2 checks");
  });
});

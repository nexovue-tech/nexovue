import { describe, expect, it } from "vitest";

import type { ErrorGroupSummary } from "./api";
import {
  countNewInWindow,
  DEPLOY_IS_NOT_A_CAUSE,
  describeErrors,
  describeOccurrences,
  describePrecedingDeploy,
  describeServices,
  firstWithStacktrace,
  isNewInWindow,
  stackFrames,
  totalOccurrences,
} from "./error-groups";

function group(overrides: Partial<ErrorGroupSummary> = {}): ErrorGroupSummary {
  return {
    group_id: "82fe32c42683a275",
    exception_type: "AuthorizationDeclined",
    message_template: "card authorization declined by upstream",
    latest_message: "card authorization declined by upstream",
    grouped_by: "type_and_message_template",
    occurrences: 158,
    first_seen: "2026-08-28T19:58:31.447Z",
    started_in_window: true,
    last_seen: "2026-08-28T20:11:00.411Z",
    services: ["demo-payments"],
    sample_trace_id: "89862496c280c749485ae30629e9737f",
    sample_span_id: "c8f8acc1e53ceb86",
    sample_source: "log",
    // The ordinary case: a project that posts no deploy notifications.
    preceding_deploy: null,
    ...overrides,
  };
}

describe("isNewInWindow and countNewInWindow", () => {
  it("reports what the API decided, rather than deciding again from first_seen", () => {
    // The margin that separates "started here" from "was already running" lives
    // in the API, because the "only new" filter needs the same one. Re-deriving
    // it here from `first_seen` is how the two came to be able to disagree — and
    // the visible symptom would be the filter hiding a row marked as new.
    const groups = [
      group({ group_id: "fresh", started_in_window: true }),
      group({
        group_id: "old",
        started_in_window: false,
        first_seen: "2026-08-28T15:00:00.000Z",
      }),
    ];

    expect(isNewInWindow(groups[0])).toBe(true);
    expect(isNewInWindow(groups[1])).toBe(false);
    expect(countNewInWindow(groups)).toBe(1);
  });

  it("counts nothing when the window held no new group", () => {
    expect(countNewInWindow([group({ started_in_window: false })])).toBe(0);
  });
});

describe("totalOccurrences", () => {
  it("adds up what the grouping collapsed", () => {
    expect(totalOccurrences([group(), group({ occurrences: 42 })])).toBe(200);
    expect(totalOccurrences([])).toBe(0);
  });
});

describe("describeErrors", () => {
  it("says both numbers, because the grouping is the point", () => {
    const caption = describeErrors([group()], "the last 6 hours");

    expect(caption).toBe(
      "1 distinct exception · 158 occurrences in the last 6 hours, 1 of which started inside it.",
    );
  });

  it("pluralises on each number independently", () => {
    const caption = describeErrors(
      [group({ occurrences: 1 }), group({ group_id: "second", occurrences: 3 })],
      "the last hour",
    );

    expect(caption).toContain("2 distinct exceptions");
    expect(caption).toContain("4 occurrences");
  });

  it("distinguishes a backlog from an incident", () => {
    const caption = describeErrors(
      [group({ first_seen: "2026-08-28T15:00:00.000Z", started_in_window: false })],
      "the last 6 hours",
    );

    expect(caption).toContain("None of them started here.");
  });

  it("says nothing threw rather than reporting zeroes", () => {
    expect(describeErrors([], "the last hour")).toBe("Nothing has thrown in the last hour.");
  });
});

describe("describeServices", () => {
  it("names the services while the names still fit", () => {
    expect(describeServices(["demo-payments"])).toBe("demo-payments");
    expect(describeServices(["demo-payments", "demo-checkout"])).toBe(
      "demo-payments, demo-checkout",
    );
  });

  it("collapses to a count once the list stops being readable", () => {
    expect(describeServices(["a", "b", "c", "d"])).toBe("a +3");
  });

  it("shows an absent list as absent", () => {
    expect(describeServices([])).toBe("—");
  });
});

describe("firstWithStacktrace", () => {
  it("skips the newer instances that carried no stack", () => {
    const carrier = firstWithStacktrace([
      { stacktrace: "" },
      { stacktrace: "   " },
      { stacktrace: "Traceback…" },
    ]);

    expect(carrier?.stacktrace).toBe("Traceback…");
  });

  it("returns nothing when no instance carried one", () => {
    expect(firstWithStacktrace([{ stacktrace: "" }])).toBeUndefined();
    expect(firstWithStacktrace([])).toBeUndefined();
  });
});

const frameText = (stacktrace: string) => stackFrames(stacktrace).map((frame) => frame.text);

describe("stackFrames", () => {
  it("splits a trace into the lines it is rendered as", () => {
    expect(frameText('File "a.py", line 1\n    raise Boom\nBoom: it broke')).toEqual([
      'File "a.py", line 1',
      "    raise Boom",
      "Boom: it broke",
    ]);
  });

  it("drops the trailing newline rather than rendering an empty last frame", () => {
    expect(frameText("one\ntwo\n")).toEqual(["one", "two"]);
  });

  it("treats an absent trace as no frames, not one blank one", () => {
    expect(stackFrames("")).toEqual([]);
    expect(stackFrames("   \n ")).toEqual([]);
  });

  it("gives two identical frames different ids, because a recursion repeats them", () => {
    const ids = stackFrames("  at loop()\n  at loop()").map((frame) => frame.id);

    expect(new Set(ids).size).toBe(2);
  });
});

describe("describeOccurrences", () => {
  it("carries an identity for records that have none of their own", () => {
    // Under `logs/dup` one throw arrives twice, identical in every field.
    const duplicate = { trace_id: "abc", span_id: "def" };
    const described = describeOccurrences([duplicate, duplicate]);

    expect(described.map((entry) => entry.id)).toEqual(["occurrence-0", "occurrence-1"]);
    expect(described.map((entry) => entry.occurrence)).toEqual([duplicate, duplicate]);
  });
});

describe("describePrecedingDeploy", () => {
  const deployed = (deployedAt: string) => ({
    version: "2026.09.06-1",
    service_name: "demo-payments",
    deployed_at: deployedAt,
  });

  it("states the order and the gap, and never a cause", () => {
    const described = describePrecedingDeploy(
      group({
        first_seen: "2026-08-28T19:58:31.447Z",
        preceding_deploy: deployed("2026-08-28T19:54:31.447Z"),
      }),
    );

    expect(described).toBe("4 minutes after deploy 2026.09.06-1 on demo-payments");
    expect(described).not.toContain("caus");
  });

  it("says nothing at all when no deploy preceded the group", () => {
    // Most projects post no deploy notification, and a sentence explaining the
    // absence would then be on every row in the product.
    expect(describePrecedingDeploy(group({ preceding_deploy: null }))).toBeUndefined();
  });

  it("keeps the fact when the gap is too long to be a coincidence worth timing", () => {
    const described = describePrecedingDeploy(
      group({
        first_seen: "2026-08-28T19:58:31.447Z",
        preceding_deploy: deployed("2026-08-19T19:58:31.447Z"),
      }),
    );

    expect(described).toBe("9 days after deploy 2026.09.06-1 on demo-payments");
  });

  it("names the deploy even when the two instants cannot be subtracted", () => {
    const described = describePrecedingDeploy(
      group({ first_seen: "not a timestamp", preceding_deploy: deployed("also not one") }),
    );

    expect(described).toBe("After deploy 2026.09.06-1 on demo-payments");
  });
});

describe("DEPLOY_IS_NOT_A_CAUSE", () => {
  it("says what the link is not, because a reader who guesses reverts the wrong release", () => {
    expect(DEPLOY_IS_NOT_A_CAUSE).toContain("not evidence that it caused this");
  });
});

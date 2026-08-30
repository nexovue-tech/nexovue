import { describe, expect, it } from "vitest";

import type { TimeWindow } from "@/shared/selection";

import type { ErrorGroupSummary } from "./api";
import {
  countNewInWindow,
  describeErrors,
  describeOccurrences,
  describeServices,
  firstWithStacktrace,
  formatOccurrences,
  groupStart,
  isNewInWindow,
  stackFrames,
  totalOccurrences,
} from "./error-groups";

/** A six-hour window, the one the demo data actually falls in. */
const WINDOW: TimeWindow = {
  from: "2026-08-28T15:00:00.000Z",
  to: "2026-08-28T21:00:00.000Z",
};

function group(overrides: Partial<ErrorGroupSummary> = {}): ErrorGroupSummary {
  return {
    group_id: "82fe32c42683a275",
    exception_type: "AuthorizationDeclined",
    message_template: "card authorization declined by upstream",
    latest_message: "card authorization declined by upstream",
    grouped_by: "type_and_message_template",
    occurrences: 158,
    first_seen: "2026-08-28T19:58:31.447Z",
    last_seen: "2026-08-28T20:11:00.411Z",
    services: ["demo-payments"],
    sample_trace_id: "89862496c280c749485ae30629e9737f",
    sample_span_id: "c8f8acc1e53ceb86",
    sample_source: "log",
    ...overrides,
  };
}

describe("groupStart", () => {
  it("calls a group that first appeared well inside the window new", () => {
    expect(groupStart("2026-08-28T19:58:31.447Z", WINDOW)).toBe("started-in-window");
  });

  it("refuses to invent a start time for a group already running when the window opened", () => {
    // The query only sees rows inside the window, so first_seen pins to its edge. The
    // honest answer is "before this window", never "started six hours ago".
    expect(groupStart("2026-08-28T15:00:00.100Z", WINDOW)).toBe("before-window");
  });

  it("scales the edge with the window rather than using one fixed span", () => {
    const fifteenMinutes: TimeWindow = {
      from: "2026-08-28T20:45:00.000Z",
      to: "2026-08-28T21:00:00.000Z",
    };
    // Ten seconds in: inside a 2% edge of 18s here, well past the 7-minute edge of a
    // six-hour window — so the same offset reads differently, which is the point.
    expect(groupStart("2026-08-28T20:45:10.000Z", fifteenMinutes)).toBe("before-window");
    expect(groupStart("2026-08-28T20:45:30.000Z", fifteenMinutes)).toBe("started-in-window");
  });

  it("treats an unparseable timestamp as older than the window, never as new", () => {
    expect(groupStart("not a timestamp", WINDOW)).toBe("before-window");
  });
});

describe("isNewInWindow and countNewInWindow", () => {
  it("counts only the groups that began inside the window", () => {
    const groups = [
      group({ group_id: "fresh" }),
      group({ group_id: "old", first_seen: "2026-08-28T15:00:00.000Z" }),
    ];

    expect(isNewInWindow(groups[0], WINDOW)).toBe(true);
    expect(isNewInWindow(groups[1], WINDOW)).toBe(false);
    expect(countNewInWindow(groups, WINDOW)).toBe(1);
  });
});

describe("formatOccurrences", () => {
  it("groups the digits so a five-figure count is readable", () => {
    expect(formatOccurrences(158)).toBe("158");
    expect(formatOccurrences(12_483)).toBe("12,483");
  });

  it("handles a count that is not a count", () => {
    expect(formatOccurrences(Number.NaN)).toBe("0");
    expect(formatOccurrences(-4)).toBe("0");
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
    const caption = describeErrors([group()], "6 hours", WINDOW);

    expect(caption).toBe(
      "1 distinct exception · 158 occurrences in the last 6 hours, 1 of which started inside it.",
    );
  });

  it("pluralises on each number independently", () => {
    const caption = describeErrors(
      [group({ occurrences: 1 }), group({ group_id: "second", occurrences: 3 })],
      "hour",
      WINDOW,
    );

    expect(caption).toContain("2 distinct exceptions");
    expect(caption).toContain("4 occurrences");
  });

  it("distinguishes a backlog from an incident", () => {
    const caption = describeErrors(
      [group({ first_seen: "2026-08-28T15:00:00.000Z" })],
      "6 hours",
      WINDOW,
    );

    expect(caption).toContain("None of them started here.");
  });

  it("says nothing threw rather than reporting zeroes", () => {
    expect(describeErrors([], "hour", WINDOW)).toBe("Nothing has thrown in the last hour.");
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

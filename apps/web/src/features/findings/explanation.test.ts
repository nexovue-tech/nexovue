import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api-client";

import type { ExplanationFact, FindingExplanation } from "./api";
import { explanationRefetchMs } from "./api";
import {
  basisLabel,
  changeAnnouncement,
  headerLine,
  listedFacts,
  lookNext,
  plainText,
  readAgainWait,
  readLine,
  refusedUntil,
  setupStep,
  staleLine,
  WORDS,
} from "./explanation";

/**
 * F3's explanation as words, pure (plan T1.11; spec *States*; AC66, AC67, AC68, AC69,
 * AC80, AC84, AC86, AC87, AC88, AC92's page case).
 */

const NOW = Date.parse("2026-11-09T14:10:00Z");
const STARTED = "2026-11-09T14:07:00Z";
const LATE = "2026-11-09T13:50:00Z";

function explanation(overrides: Partial<FindingExplanation> = {}): FindingExplanation {
  return {
    finding_id: "f-1",
    state: "read",
    version: 1,
    latest_version: 1,
    read_at: "2026-11-09T14:08:00Z",
    reads_failed: 0,
    facts: [],
    likely_cause: null,
    cause_held: false,
    previous_cause: null,
    shared_cause_count: 0,
    wording_state: "off",
    wording: null,
    next_read_at: null,
    ...overrides,
  };
}

function fact(overrides: Partial<ExplanationFact> = {}): ExplanationFact {
  return {
    id: "deploy:d-1",
    number: 1,
    kind: "deploy",
    basis: "verified",
    sentence: [{ text: "Deploy of " }, { name: "checkout" }],
    at: null,
    read_at: null,
    role: null,
    absence: null,
    read: null,
    is_cause: false,
    supports_cause: false,
    ...overrides,
  };
}

const absence = (reason: string, read: string | null = null) =>
  fact({ id: `absence:${reason}:${read}`, kind: "absence", basis: null, absence: reason, read });

const CAUSE = {
  evidence_id: "deploy:d-1",
  rule: "signed_deploy",
  strength: "likely" as const,
  basis: "verified" as const,
  same_minute: false,
  header: [{ text: "Likely: the deploy of " }, { name: "checkout" }],
  card: [{ text: "Likely: deploy of " }, { name: "checkout" }],
};

const line = (value: FindingExplanation | undefined, failed = false, started = STARTED) =>
  headerLine(value, { failed, finding: { started_at: started }, now: NOW });

describe("the header line, in every state (AC86)", () => {
  it.each([
    ["loading", undefined, false, STARTED, WORDS.reading],
    ["could not load", undefined, true, STARTED, WORDS.notLoaded],
    ["reading", explanation({ state: "reading" }), false, STARTED, WORDS.reading],
    [
      "still reading after 5 minutes",
      explanation({ state: "reading" }),
      false,
      LATE,
      WORDS.stillReading,
    ],
    ["job expired", explanation({ state: "not_read" }), false, STARTED, WORDS.notRead],
    ["nothing found", explanation(), false, STARTED, WORDS.nothingFound],
    ["a read failed (AC87)", explanation({ reads_failed: 1 }), false, STARTED, WORDS.failedReads],
    ["a cause held (FR50, AC92)", explanation({ cause_held: true }), false, STARTED, WORDS.held],
  ])("%s", (_name, value, failed, started, words) => {
    expect(plainText(line(value, failed, started).segments)).toBe(words);
  });

  it("names the cause by its own segments, and a held one never reads 'Not enough evidence'", () => {
    expect(line(explanation({ likely_cause: CAUSE })).kind).toBe("cause");
    expect(plainText(line(explanation({ likely_cause: CAUSE })).segments)).toBe(
      "Likely: the deploy of checkout",
    );
    expect(
      plainText(line(explanation({ cause_held: true, reads_failed: 1 })).segments),
    ).not.toContain("Not enough evidence");
  });

  it("never says 'Not enough evidence' with every read failed (AC87)", () => {
    const failed = explanation({ reads_failed: 4, facts: [absence("could_not_read", "errors")] });
    expect(plainText(line(failed).segments)).toBe(WORDS.failedReads);
    expect(setupStep(failed)).toBeNull();
  });
});

describe("the facts as listed (FR44, AC84)", () => {
  it("leaves F28's release rows to the first row, and collapses repeated setup absences only", () => {
    const listed = listedFacts([
      fact({ id: "release:p", role: "last_before" }),
      fact(),
      absence("no_dependency_traces"),
      absence("no_dependency_traces"),
      absence("could_not_read", "errors"),
      absence("could_not_read", "hosts"),
    ]);
    expect(listed.map((each) => each.id)).toEqual([
      "deploy:d-1",
      "absence:no_dependency_traces:null",
      "absence:could_not_read:errors",
      "absence:could_not_read:hosts",
    ]);
  });

  it("labels each basis in words, a signed deploy apart from a seen version (AC67)", () => {
    expect(basisLabel(fact())).toBe("Signed deploy record");
    expect(basisLabel(fact({ basis: "telemetry_text" }))).toBe("Version seen in telemetry");
    expect(basisLabel(fact({ kind: "check" }))).toBe("nexovue's own check");
    expect(basisLabel(fact({ kind: "dependency", basis: "telemetry" }))).toBe(
      "Counted from telemetry",
    );
    expect(basisLabel(fact({ kind: "error_group", basis: "telemetry_text" }))).toBe(
      "Read from telemetry text",
    );
    expect(basisLabel(absence("truncated"))).toBeNull();
  });
});

describe("the setup step (FR45, AC84)", () => {
  it("names the top setup absence when nothing was found", () => {
    const value = explanation({
      facts: [absence("no_dependency_traces"), absence("no_deploy_information")],
    });
    expect(setupStep(value)).toEqual({ label: "Send deploy notifications", doc: "deploys" });
  });

  it("offers none beside a cause, a held cause or a failed read", () => {
    const facts = [absence("no_deploy_information")];
    expect(setupStep(explanation({ facts, likely_cause: CAUSE }))).toBeNull();
    expect(setupStep(explanation({ facts, cause_held: true }))).toBeNull();
    expect(setupStep(explanation({ facts, reads_failed: 1 }))).toBeNull();
  });
});

describe("times, in the project's zone, named (AC68, AC80, AC88)", () => {
  // A time on another day carries its date, so these read as of NOW's day, not the day the
  // suite runs: written on 9 November, they failed on every later day.
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("changes with the project's zone and carries its name", () => {
    const value = explanation();
    expect(readLine(value, "Asia/Kolkata")).toMatch(/19:38 (IST|Kolkata time)/);
    expect(readLine(value, "UTC")).toMatch(/14:08 UTC/);
  });

  it("says why reading again waits, and when it may", () => {
    expect(
      readAgainWait("2026-11-09T14:09:00Z", "2026-11-09T14:08:00Z", {
        zone: "UTC",
        now: NOW - 120_000,
      }),
    ).toBe("Read at 14:08 UTC. You can read again at 14:09 UTC");
    expect(readAgainWait("2026-11-09T14:09:00Z", null, { zone: "UTC", now: NOW })).toBeNull();
  });

  it("reads a refusal's next time from its 409 (AC80)", () => {
    const refused = new ApiError("Too soon", 409, { correlationId: "c", traceId: "t" } as never, {
      type: "about:blank",
      title: "Conflict",
      status: 409,
      next_read_at: "2026-11-09T14:09:00Z",
    });
    expect(refusedUntil(refused)).toBe("2026-11-09T14:09:00Z");
    expect(refusedUntil(new Error("other"))).toBeNull();
  });

  it("keeps the held read's time when a refresh fails (AC88)", () => {
    expect(staleLine(explanation(), "UTC")).toBe(
      "Read at 14:08 UTC. Could not refresh; still trying",
    );
  });

  it("states how many open findings share the cause, from two up (FR47)", () => {
    expect(readLine(explanation({ likely_cause: CAUSE, shared_cause_count: 3 }), "UTC")).toBe(
      "Read at 14:08 UTC · 3 open findings name this likely cause",
    );
    expect(readLine(explanation({ likely_cause: CAUSE, shared_cause_count: 1 }), "UTC")).toBe(
      "Read at 14:08 UTC",
    );
  });
});

describe("a page left open (AC69)", () => {
  it("announces a new version, and a changed cause by its words", () => {
    expect(changeAnnouncement(explanation(), explanation())).toBeNull();
    expect(changeAnnouncement(explanation(), explanation({ version: 2 }))).toBe(
      "The facts were read again.",
    );
    expect(
      changeAnnouncement(explanation(), explanation({ version: 2, likely_cause: CAUSE })),
    ).toBe("The facts were read again; the likely cause changed: Likely: the deploy of checkout");
  });

  it("polls fast for 15 minutes, then slowly while open, and not once closed", () => {
    const opened = Date.parse(STARTED);
    expect(explanationRefetchMs({ state: "open", started_at: STARTED }, opened + 60_000)).toBe(
      5_000,
    );
    expect(explanationRefetchMs({ state: "open", started_at: STARTED }, opened + 16 * 60_000)).toBe(
      60_000,
    );
    expect(explanationRefetchMs({ state: "closed", started_at: STARTED }, opened)).toBe(false);
  });
});

describe("Look next (FR42, AC66)", () => {
  it("opens the service on every screen, and the whole project's infrastructure", () => {
    expect(lookNext({ scope: "service", service_name: "checkout" })).toEqual([
      { label: "Traces", to: "/traces", search: { service: "checkout" } },
      { label: "Logs", to: "/logs", search: { service: "checkout" } },
      { label: "Errors", to: "/errors", search: { service_name: "checkout" } },
      { label: "Deploys", to: "/releases", search: {} },
      { label: "Infrastructure (whole project)", to: "/infrastructure", search: {} },
    ]);
  });

  it("opens the project for a project finding", () => {
    expect(
      lookNext({ scope: "project", service_name: null }).every(
        (link) => !Object.keys(link.search).length,
      ),
    ).toBe(true);
  });
});

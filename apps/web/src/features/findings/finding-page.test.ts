import { describe, expect, it } from "vitest";

import { bandStretches } from "@/shared/components/chart-band";

import type { FindingSignal, SignalSeries } from "./api";
import {
  bandSentence,
  describeSignal,
  evidenceSentence,
  ownEndpoint,
  PLOT_LINES,
  signalPlot,
} from "./finding-page";
import { finding } from "./render-harness.test-util";

/**
 * What the finding page draws and offers, apart from how (FR31). AC38: each signal's
 * readings are drawn against its normal band, from the normal to the edge the
 * sensitivity's minimum change sets.
 */

const AT = ["2026-11-01T10:00:00Z", "2026-11-01T10:01:00Z", "2026-11-01T10:02:00Z"];
const seconds = AT.map((at) => Date.parse(at) / 1000);

const slower: SignalSeries = {
  signal: "slower",
  unit: "ms",
  points: [
    { at: AT[0] ?? "", value: 112, normal: 110, edge: 165 },
    { at: AT[1] ?? "", value: 330, normal: 120, edge: 180 },
    { at: AT[2] ?? "", value: null, normal: null, edge: null },
  ],
};

const traffic: SignalSeries = {
  signal: "traffic",
  unit: "rpm",
  points: [
    { at: AT[0] ?? "", value: 400, normal: 1000, edge: 500 },
    { at: AT[1] ?? "", value: 300, normal: 1000, edge: 500 },
  ],
};

describe("a signal's chart (AC38)", () => {
  it("plots the readings, the normal and the band's edge on one scale", () => {
    const plot = signalPlot(slower);

    expect(PLOT_LINES).toEqual(["This finding", "Normal", "Edge of the normal band"]);
    expect(plot.data).toEqual([seconds, [112, 330, null], [110, 120, null], [165, 180, null]]);
  });

  it("fills the band from the normal up to the edge, where slower means higher", () => {
    const plot = signalPlot(slower);

    expect(plot.band).toEqual({ upper: 3, lower: 2 });
    expect(bandStretches(plot.data, plot.band)).toEqual([
      { xs: seconds.slice(0, 2), upper: [165, 180], lower: [110, 120] },
    ]);
  });

  it("fills it from the edge up to the normal for losing traffic, which is judged below it", () => {
    const plot = signalPlot(traffic);

    expect(plot.band).toEqual({ upper: 2, lower: 3 });
    const [stretch] = bandStretches(plot.data, plot.band);
    expect(stretch?.upper).toEqual([1000, 1000]);
    expect(stretch?.lower).toEqual([500, 500]);
  });

  it("draws no band at all for a finding whose readings have expired (AC40)", () => {
    const plot = signalPlot({ ...slower, points: [] });

    expect(bandStretches(plot.data, plot.band)).toEqual([]);
  });

  it("is the series the page draws for its finding", () => {
    const [series] = finding().series;
    if (!series) throw new Error("the fixture has a series");

    expect(bandStretches(signalPlot(series).data, signalPlot(series).band)).toHaveLength(1);
  });
});

const stored: FindingSignal = {
  signal: "slower",
  key: "slower",
  normal: 110,
  band_low: 110,
  band_high: 165,
  current: 330,
  worst: 412,
  unit: "ms",
  comparison: "Tuesdays around 15:00, last 3 weeks",
  endpoint: "POST /pay",
  endpoint_current: 400,
  endpoint_last_week: 100,
};

describe("a signal's evidence endpoint (FR3, FR31)", () => {
  it("names a slower endpoint's p95 against the same time last week", () => {
    expect(evidenceSentence(stored)).toBe(
      "POST /pay: p95 400ms, against 100ms the same time last week.",
    );
  });

  it("names a failing endpoint's share failing against the same time last week", () => {
    const failing: FindingSignal = {
      ...stored,
      signal: "failing",
      unit: "%",
      endpoint_current: 12.5,
      endpoint_last_week: 0,
    };

    expect(evidenceSentence(failing)).toBe(
      "POST /pay: 12.5% failing, against 0.0% the same time last week.",
    );
  });

  it("writes a value it does not have as the em dash, never a zero", () => {
    const older = { ...stored, endpoint_current: null, endpoint_last_week: null };

    expect(evidenceSentence(older)).toBe("POST /pay: p95 —, against — the same time last week.");
  });

  it("says nothing for a signal that names no endpoint", () => {
    expect(evidenceSentence({ ...stored, endpoint: null })).toBeNull();
  });
});

describe("a signal's stored band (FR16)", () => {
  it("reads from the normal to its edge, in the signal's unit", () => {
    expect(bandSentence(stored)).toBe("Normal band 110ms to 165ms.");
  });

  it("is the em dash for a finding stored before its band was kept", () => {
    expect(bandSentence({ ...stored, band_low: null, band_high: null })).toBe("Normal band —.");
  });
});

describe("ownEndpoint", () => {
  const base = finding().signals[0] as FindingSignal;

  it("names the endpoint only on an endpoint's own entry, not on a service's evidence", () => {
    expect(
      ownEndpoint({ ...base, signal: "failing", key: "endpoint:POST /pay", endpoint: "POST /pay" }),
    ).toBe("POST /pay");
    expect(
      ownEndpoint({ ...base, signal: "failing", key: "failing", endpoint: "GET /x" }),
    ).toBeNull();
    expect(
      ownEndpoint({ ...base, signal: "slower", key: "slower", endpoint: "GET /x" }),
    ).toBeNull();
  });

  it("keeps an entry stored before keys were kept as it was", () => {
    expect(ownEndpoint({ ...base, key: null, endpoint: "GET /x" })).toBe("GET /x");
  });
});

describe("a traffic reading says what it counts", () => {
  const entry = {
    signal: "traffic",
    unit: "rpm",
    worst: 4,
    normal: 24,
    current: 7.2,
    band_low: 12,
    band_high: 24,
  } as unknown as FindingSignal;

  it("names requests a minute once in each sentence", () => {
    expect(describeSignal(entry)).toBe(
      "4.0 requests a minute at worst, against 24 normally. Now 7.2.",
    );
    expect(bandSentence(entry)).toBe("Normal band 12 to 24 requests a minute.");
  });
});

// ---------------------------------------------------------------- the case

import type { FindingDetail } from "./api";
import {
  caseActions,
  caseDoneSentence,
  caseSentence,
  eventSentence,
  joinedLeadLine,
} from "./finding-page";
import { NOBODY_HAS_TAKEN_THIS, resolutionWords } from "./finding-words";

const ME = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001";
const ASHA = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0009";
const NOW = Date.parse("2026-11-02T18:00:00Z");
const ZONE = "UTC";

const aCase = (overrides: Partial<FindingDetail>): FindingDetail =>
  finding({
    started_at: "2026-11-02T14:02:00Z",
    ...overrides,
  });

const sentence = (root: FindingDetail, viewerId: string | null = ME) =>
  caseSentence({ finding: root, root, viewerId, zone: ZONE, now: NOW });

const investigating = (overrides: Partial<FindingDetail> = {}) =>
  aCase({
    case_state: "investigating",
    case_opened_at: "2026-11-02T14:06:00Z",
    assignee_id: ASHA,
    assignee_name: "Asha",
    case_taken_at: "2026-11-02T14:06:00Z",
    ...overrides,
  });

describe("the page's one sentence states both questions (FR23, AC5, AC63)", () => {
  it("never claims nobody is working: the words are the fixed ones", () => {
    expect(sentence(aCase({}))).toBe(`Open since 14:02 UTC. ${NOBODY_HAS_TAKEN_THIS}`);
  });

  it("an assigned open case is a fact with its time (D76)", () => {
    expect(sentence(investigating())).toBe("Open since 14:02 UTC. Asha took this at 14:06 UTC");
    expect(sentence(investigating({ assignee_id: ME, assignee_name: "Me" }))).toBe(
      "Open since 14:02 UTC. You took this at 14:06 UTC",
    );
  });

  it("an unassigned case reads as being investigated by nobody", () => {
    expect(sentence(investigating({ assignee_id: null, assignee_name: null }))).toBe(
      "Open since 14:02 UTC. Being investigated, nobody assigned",
    );
  });

  it("recovery does not end the work, and the words follow the closed reason (AC5)", () => {
    const recovered = investigating({
      state: "closed",
      closed_reason: "recovered",
      closed_at: "2026-11-02T15:04:00Z",
      case_signal: "closed",
    });
    expect(sentence(recovered)).toBe("Recovered at 15:04 UTC, still being investigated by Asha");
    expect(sentence({ ...recovered, closed_reason: "service_gone" } as FindingDetail)).toBe(
      "Service stopped sending at 15:04 UTC, still being investigated by Asha",
    );
    expect(sentence({ ...recovered, assignee_id: null, assignee_name: null })).toBe(
      "Recovered at 15:04 UTC, still being investigated, nobody assigned",
    );
  });

  it("a root with an open joined finding never reads Recovered (AC59, AC100)", () => {
    const back = investigating({
      state: "closed",
      closed_reason: "recovered",
      closed_at: "2026-11-02T15:04:00Z",
      case_signal: "open",
      open_joined_finding_id: "f-2",
      open_joined_since: "2026-11-02T15:50:00Z",
      assignee_acted_since_join: true,
    });
    expect(sentence(back)).toBe(
      "Came back at 15:50 UTC and is still open. Asha took the case at 14:06 UTC",
    );
    const idle = { ...back, assignee_acted_since_join: false } as FindingDetail;
    expect(sentence(idle)).toContain("Asha took the case");
    expect(sentence(idle)).toContain(", and has not acted since");
    expect(sentence(idle)).not.toContain("Recovered");
  });

  it("a resolved case says how it ended, on an open or a recovered signal", () => {
    const resolved = aCase({
      state: "closed",
      closed_reason: "recovered",
      closed_at: "2026-11-02T15:04:00Z",
      case_state: "resolved",
      case_opened_at: "2026-11-02T14:06:00Z",
      case_resolution: "resolved",
      case_resolved_at: "2026-11-02T16:10:00Z",
      case_resolved_by_name: "Asha",
      case_signal: "closed",
    });
    expect(sentence(resolved)).toBe("Recovered at 15:04 UTC. Asha resolved the case at 16:10 UTC.");
    expect(
      sentence({
        ...resolved,
        state: "open",
        closed_reason: null,
        closed_at: null,
        case_signal: "open",
      } as FindingDetail),
    ).toBe("Still open: the signal has not recovered. Asha resolved the case at 16:10 UTC.");
  });

  it("times carry the date across a day boundary, and always the zone (AC107)", () => {
    const yesterday = sentence(
      investigating({ started_at: "2026-10-31T14:02:00Z", case_taken_at: "2026-10-31T14:06:00Z" }),
    );
    expect(yesterday).toBe("Open since 31 Oct, 14:02 UTC. Asha took this at 31 Oct, 14:06 UTC");
  });
});

describe("how a case ended, one phrase per resolution", () => {
  it("spells each resolution the spec's way", () => {
    expect(resolutionWords("tracker_closed", { at: "16:10 UTC" })).toBe(
      "Closed in GitHub at 16:10 UTC, reopen here if that was wrong",
    );
    expect(resolutionWords("no_activity")).toBe("Ended by itself after 90 days with no activity");
    expect(resolutionWords("recovered_untouched")).toBe(
      "Ended by itself 3 days after recovery. Nobody had added anything",
    );
    expect(resolutionWords("false_alarm")).toBe("Ended as a false alarm");
    expect(resolutionWords("new_normal")).toBe("Ended as the new normal");
  });
});

describe("the joined page's lead line (FR12, FR13)", () => {
  it("names the case it is part of, active or resolved", () => {
    expect(joinedLeadLine(investigating(), ZONE, NOW)).toBe(
      "Part of a case that started at 14:06 UTC",
    );
    expect(
      joinedLeadLine(
        investigating({
          case_state: "resolved",
          case_resolution: "resolved",
          case_resolved_at: "2026-11-01T10:00:00Z",
        }),
        ZONE,
        NOW,
      ),
    ).toBe("Part of a case resolved on 1 Nov, 10:00 UTC");
  });
});

describe("which case actions this person gets", () => {
  it("offers Take this on a finding nobody took, and nothing to a viewer", () => {
    expect(caseActions(aCase({}), { canOperateCases: true, viewerId: ME }).takeThis).toBe(true);
    expect(caseActions(aCase({}), { canOperateCases: false, viewerId: ME })).toMatchObject({
      takeThis: false,
      resolve: false,
      note: false,
    });
  });

  it("offers Resolve and Reassign on my case, Take over on someone else's", () => {
    const mine = caseActions(investigating({ assignee_id: ME }), {
      canOperateCases: true,
      viewerId: ME,
    });
    expect(mine).toMatchObject({ resolve: true, reassign: true, takeOverFrom: null });
    const theirs = caseActions(investigating(), { canOperateCases: true, viewerId: ME });
    expect(theirs.takeOverFrom).toBe("Asha");
  });

  it("offers Reopen on a resolved case", () => {
    const resolved = investigating({
      case_state: "resolved",
      case_resolution: "resolved",
      case_resolved_at: "2026-11-02T16:10:00Z",
    });
    expect(caseActions(resolved, { canOperateCases: true, viewerId: ME })).toMatchObject({
      reopen: true,
      resolve: false,
      takeThis: false,
    });
  });
});

describe("the line after an act says only what the system knows (FR48, AC98, AC117)", () => {
  it("adds the PagerDuty lines only when the project has a PagerDuty channel", () => {
    expect(caseDoneSentence("take", { hasPagerDuty: true })).toBe(
      "Yours. PagerDuty is not told: acknowledge the page there too.",
    );
    expect(caseDoneSentence("take")).toBe("Yours.");
    expect(caseDoneSentence("resolve", { hasPagerDuty: true })).toBe(
      "Resolved. PagerDuty is not told, so a live page stays until the signal recovers.",
    );
    expect(caseDoneSentence("resolve")).toBe("Resolved.");
  });

  it("tells the truth about the assignment email, outcome by outcome", () => {
    expect(caseDoneSentence("assign", { assigneeName: "Ravi", emailOutcome: "will_try" })).toBe(
      "Assigned to Ravi. An email is on its way, so check they saw it if it is urgent.",
    );
    expect(
      caseDoneSentence("assign", { assigneeName: "Ravi", emailOutcome: "not_configured" }),
    ).toBe("Assigned to Ravi. Email is not set up here, so tell them yourself.");
    expect(caseDoneSentence("assign", { assigneeName: "Ravi", emailOutcome: "rate_limited" })).toBe(
      "Assigned to Ravi. Ravi was emailed about this case a moment ago.",
    );
  });
});

describe("a timeline row's words come from kinds and typed detail alone", () => {
  it("reads a take, a reassign and a deleted note", () => {
    expect(
      eventSentence({
        id: "e1",
        kind: "assigned",
        at: "2026-11-02T14:06:00Z",
        actor_kind: "person",
        actor_id: ASHA,
        actor_name: "Asha",
        detail: { assignee_id: ASHA },
      }),
    ).toBe("Asha took this");
    expect(
      eventSentence({
        id: "e2",
        kind: "note_deleted",
        at: "2026-11-02T14:07:00Z",
        actor_kind: "person",
        actor_name: "Asha",
        detail: {},
      }),
    ).toBe("A note was deleted");
  });
});

// ------------------------------------------------ keeping and ending (F22 T2.7)

import { caseEndingSentence, earlierCaseLine, keptSentence } from "./finding-page";

describe("an active case says when it ends by itself (FR26, FR49)", () => {
  it("an idle case says how long it has been quiet, when it ends, and what keeps it", () => {
    const idle = investigating({
      case_activity_at: "2026-08-18T18:00:00Z",
      case_ends_on: "2026-11-16T18:00:00Z",
      case_ends_by: "idle",
    });
    expect(caseEndingSentence(idle, ZONE, NOW)).toBe(
      "No activity for 76 days. Ends by itself on 16 Nov, 18:00 UTC. Add a note to keep it going, or resolve it.",
    );
  });

  it("an untouched recovered case says it ends unless someone adds something", () => {
    const untouched = investigating({
      state: "closed",
      closed_at: "2026-11-02T15:04:00Z",
      case_ends_on: "2026-11-05T15:04:00Z",
      case_ends_by: "untouched",
    });
    expect(caseEndingSentence(untouched, ZONE, NOW)).toBe(
      "Recovered. Ends by itself on 5 Nov, 15:04 UTC unless someone adds something.",
    );
  });

  it("an end today says at what time, not on what day", () => {
    const today = investigating({
      case_ends_on: "2026-11-02T21:00:00Z",
      case_ends_by: "untouched",
    });
    expect(caseEndingSentence(today, ZONE, NOW)).toBe(
      "Recovered. Ends by itself at 21:00 UTC unless someone adds something.",
    );
  });

  it("says nothing when the server names no end", () => {
    expect(caseEndingSentence(investigating(), ZONE, NOW)).toBeNull();
  });
});

describe("a finding with no case names the service's earlier one (FR9, FR13, AC15, AC68, AC95)", () => {
  const earlier = {
    id: "f-0",
    case_state: "resolved" as const,
    case_resolution: "resolved" as const,
    case_resolved_at: "2026-11-01T16:10:00Z",
    case_activity_at: "2026-11-01T16:10:00Z",
  };

  it("a quiet active case is named with when it was last touched; taking this joins it", () => {
    const line = earlierCaseLine(
      {
        ...earlier,
        case_state: "investigating",
        case_resolution: null,
        case_resolved_at: null,
        case_activity_at: "2026-10-12T09:00:00Z",
      },
      ZONE,
      NOW,
    );
    expect(line).toEqual({
      sentence: "This service has a case nobody has touched since 12 Oct, 09:00 UTC",
      reopenable: false,
    });
  });

  it("a case a person resolved is a link only", () => {
    for (const resolution of ["resolved", "false_alarm", "new_normal"] as const) {
      const line = earlierCaseLine({ ...earlier, case_resolution: resolution }, ZONE, NOW);
      expect(line.sentence).toBe("This service had a case resolved on 1 Nov, 16:10 UTC");
      expect(line.reopenable).toBe(false);
    }
  });

  it("a case that ended without a person's decision can be reopened, and says how it ended", () => {
    for (const resolution of ["tracker_closed", "no_activity", "recovered_untouched"] as const) {
      const line = earlierCaseLine({ ...earlier, case_resolution: resolution }, ZONE, NOW);
      expect(line.reopenable).toBe(true);
      expect(line.sentence).toBe(
        `This service had a case that ended on 1 Nov, 16:10 UTC. ${resolutionWords(resolution, {
          at: "1 Nov, 16:10 UTC",
        })}`,
      );
    }
  });

  it("offers Reopen that case beside Take this only when it can be reopened", () => {
    const reopenable = aCase({
      earlier_case: { ...earlier, case_resolution: "no_activity" },
    });
    const personal = aCase({ earlier_case: earlier });
    expect(caseActions(reopenable, { canOperateCases: true, viewerId: ME })).toMatchObject({
      takeThis: true,
      reopenEarlier: "f-0",
    });
    expect(caseActions(personal, { canOperateCases: true, viewerId: ME }).reopenEarlier).toBeNull();
    expect(
      caseActions(reopenable, { canOperateCases: false, viewerId: ME }).reopenEarlier,
    ).toBeNull();
  });
});

describe("a case says how long it is kept (FR24, FR27, AC112)", () => {
  it("names the deletion date and the joined findings still kept", () => {
    const resolved = aCase({
      case_state: "resolved",
      deletes_on: "2026-12-02T16:10:00Z",
      joined_kept_count: 3,
    });
    expect(keptSentence(resolved, ZONE, NOW)).toBe(
      "Kept until 2 Dec, 16:10 UTC. 3 later findings are still kept.",
    );
    expect(keptSentence({ ...resolved, joined_kept_count: 1 }, ZONE, NOW)).toBe(
      "Kept until 2 Dec, 16:10 UTC. 1 later finding is still kept.",
    );
  });

  it("an active case names only its kept findings, and a case with none says nothing", () => {
    expect(keptSentence(investigating({ joined_kept_count: 2 }), ZONE, NOW)).toBe(
      "2 later findings are still kept.",
    );
    expect(keptSentence(investigating({ joined_kept_count: 0 }), ZONE, NOW)).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import {
  channelConfigOf,
  channelProblems,
  describeCondition,
  describeDestination,
  describeDuration,
  describeTold,
  emptyChannelDraft,
  emptyRuleDraft,
  fieldProblems,
  muteUntil,
  ruleDraftOf,
  ruleProblems,
  ruleRequestOf,
  ruleStatus,
  sameRule,
  withKind,
} from "./alert-words";
import { channel, incident, rule } from "./render-harness.test-util";

describe("a rule in words", () => {
  it("names every check, or the one it watches, and the states it fires on", () => {
    expect(describeCondition(rule())).toBe("Any check is down");
    expect(
      describeCondition(
        rule({
          condition: {
            kind: "check_state",
            check_ids: ["chk-1"],
            states: { down: "critical", degraded: "warning", misconfigured: "warning" },
          },
        }),
        [{ id: "chk-1", name: "Orders API" }],
      ),
    ).toBe("Orders API is down, degraded or misconfigured");
  });

  it("says a broken rule is broken before it says anything else (FR25)", () => {
    const muted = new Date(Date.now() + 3_600_000).toISOString();
    expect(ruleStatus(rule({ notifying_nobody: true, muted_until: muted })).label).toBe(
      "Not notifying anyone",
    );
    expect(ruleStatus(rule({ covers_nothing: true })).label).toBe("Covers nothing");
    expect(ruleStatus(rule({ enabled: false })).label).toBe("Off");
    expect(ruleStatus(rule({ muted_until: muted })).label).toMatch(/^Muted until /);
    expect(ruleStatus(rule()).label).toBe("Active");
  });
});

describe("an incident in words", () => {
  it("says who was told, and whose delivery failed", () => {
    expect(describeTold(incident())).toBe("Organization owners");
    const failed = incident({
      notifications: [
        { ...incident().notifications[0], channel_name: "#incidents", state: "failed" },
      ],
    });
    expect(describeTold(failed)).toBe("#incidents (failed)");
    expect(describeTold(incident({ muted: true }))).toBe("Muted — nobody was told");
    expect(describeTold(incident({ notifications: [] }))).toBe("Nobody was told");
  });

  it("measures coarsely", () => {
    const start = "2026-09-19T10:00:00Z";
    expect(describeDuration(start, "2026-09-19T10:00:20Z")).toBe("1 min");
    expect(describeDuration(start, "2026-09-19T12:05:00Z")).toBe("2 h 5 min");
    expect(describeDuration(start, "2026-09-19T13:00:00Z")).toBe("3 h");
  });
});

describe("a rule draft", () => {
  it("starts from the defaults, prefilled with the check it came from", () => {
    const draft = emptyRuleDraft({ checkId: "chk-1", checkName: "Orders API" });
    expect(draft).toMatchObject({
      name: "Orders API is down",
      scope: "chosen",
      checkIds: ["chk-1"],
      states: { down: "critical" },
    });
    expect(sameRule(draft, emptyRuleDraft({ checkId: "chk-1", checkName: "Orders API" }))).toBe(
      true,
    );
  });

  it("refuses to save a rule that would tell nobody", () => {
    expect(ruleProblems(emptyRuleDraft())).toMatchObject({
      name: expect.any(String),
      channels: "Choose where to send it. A rule with no channel tells nobody.",
    });
  });

  it("round-trips a stored rule and sends null for every check", () => {
    const stored = rule();
    const draft = ruleDraftOf(stored);
    expect(sameRule(draft, ruleDraftOf(stored))).toBe(true);
    expect(ruleRequestOf(draft, "p")).toEqual({
      project_id: "p",
      name: "Any check down",
      condition: { kind: "check_state", check_ids: null, states: { down: "critical" } },
      channel_ids: ["ch-owners"],
      renotify_minutes: null,
      enabled: true,
    });
  });

  it("treats the same channels in another order as unchanged", () => {
    const baseline = { ...emptyRuleDraft(), channelIds: ["a", "b"] };
    expect(sameRule({ ...baseline, channelIds: ["b", "a"] }, baseline)).toBe(true);
  });
});

describe("a channel draft", () => {
  it("asks for what each kind needs", () => {
    expect(channelProblems({ ...emptyChannelDraft("slack"), name: "#ops" })).toEqual({
      secret: "Paste the incoming webhook url.",
    });
    expect(
      channelProblems({ ...emptyChannelDraft("slack"), name: "#ops", secret: "http://x" }).secret,
    ).toBe("It must be an https:// URL.");
    expect(channelProblems({ ...emptyChannelDraft("email"), name: "On call" })).toEqual({
      addresses: "Add at least one address.",
    });
    expect(channelProblems({ ...emptyChannelDraft("webhook"), name: "Bot" }).url).toBe(
      "It must be an https:// URL.",
    );
  });

  it("splits addresses however they were pasted", () => {
    expect(
      channelConfigOf({ ...emptyChannelDraft("email"), addresses: "a@x.test, b@x.test\nc@x.test" }),
    ).toEqual({ addresses: ["a@x.test", "b@x.test", "c@x.test"] });
  });

  it("shows where a channel sends as far as may be shown", () => {
    expect(describeDestination(channel())).toBe("hooks.slack.com");
    expect(describeDestination(channel({ kind: "owners" }))).toBe("Every owner's verified address");
  });
});

describe("mutes", () => {
  it("end tomorrow at nine when asked to", () => {
    const now = new Date(2026, 8, 19, 22, 30);
    const until = muteUntil("tomorrow", now);
    expect([until.getDate(), until.getHours(), until.getMinutes()]).toEqual([20, 9, 0]);
    expect(muteUntil("1h", now).getTime() - now.getTime()).toBe(3_600_000);
  });
});

describe("the API's field errors", () => {
  it("land on the form's own fields and nowhere else", () => {
    const error = new ApiError(
      "refused",
      422,
      { correlationId: "c", method: "POST", path: "/x" } as never,
      {
        type: "about:blank",
        title: "Refused",
        status: 422,
        detail: "cannot save",
        errors: [
          { field: "secret", reason: "must be an https:// URL" },
          { field: "config.unknown", reason: "ignored" },
        ],
      } as never,
    );
    expect(
      fieldProblems<"secret">(error, (apiField) => (apiField === "secret" ? "secret" : undefined)),
    ).toEqual({ secret: "Must be an https:// URL." });
  });
});

describe("service rules in words", () => {
  it("say the reading, the threshold and the wait", () => {
    expect(
      describeCondition(
        rule({
          condition: {
            kind: "service_error_rate",
            services: ["checkout"],
            threshold_percent: 5,
            window_minutes: 5,
            min_requests: 20,
            fire_after_minutes: 5,
            resolve_after_minutes: 5,
            severity: "critical",
          },
        }),
      ),
    ).toBe("checkout error rate at or over 5% for 5 min");
    expect(
      describeCondition(
        rule({
          condition: {
            kind: "service_silent",
            services: null,
            silent_minutes: 10,
            severity: "critical",
          },
        }),
      ),
    ).toBe("Any service sends nothing for 10 min");
    expect(
      describeCondition(
        rule({ condition: { kind: "new_error_group", services: null, severity: "warning" } }),
      ),
    ).toBe("A new error in any service");
  });

  it("refuse a threshold that could never be reached", () => {
    const draft = withKind(
      { ...emptyRuleDraft(), name: "x", channelIds: ["c"] },
      "service_error_rate",
    );
    expect(ruleProblems({ ...draft, threshold: "150" }).threshold).toBe(
      "A percentage above 0 and at most 100.",
    );
    expect(ruleProblems({ ...draft, minRequests: "0" }).minRequests).toBe("At least 1.");
    expect(ruleProblems(draft)).toEqual({});
  });

  it("round-trip through a stored rule", () => {
    const stored = rule({
      condition: {
        kind: "service_latency_p95",
        services: ["cart"],
        threshold_ms: 750,
        window_minutes: 10,
        min_requests: 50,
        fire_after_minutes: 3,
        resolve_after_minutes: 7,
        severity: "warning",
      },
    });
    expect(ruleRequestOf(ruleDraftOf(stored), "p").condition).toEqual(stored.condition);
  });
});

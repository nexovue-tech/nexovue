import { describe, expect, it } from "vitest";

import type { AuditEntry } from "./api";
import { describeEntry } from "./audit-words";

function entry(action: string, detail: Record<string, unknown>, actorName: string): AuditEntry {
  return {
    id: "1",
    at: "2026-10-11T09:00:00Z",
    actor: { kind: "person", id: null, name: actorName },
    action,
    target: "user:5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001",
    target_name: "Former member",
    detail,
    impersonation_context: null,
  };
}

describe("people who left", () => {
  // A removal no longer stores the address (personal-data spec), so the sentence
  // names the person the way the log now knows them.
  it("names a removed person by what the log calls them, not an address", () => {
    expect(describeEntry(entry("member.removed", { role: "member" }, "Raj"))).toBe(
      "Raj removed Former member (Member)",
    );
  });

  it("says a person deleted their own account", () => {
    expect(describeEntry(entry("member.left", { role: "admin" }, "Former member"))).toBe(
      "Former member deleted their account (Admin)",
    );
  });
});

describe("what people said about detection (F6)", () => {
  it("quotes a false alarm's reason", () => {
    expect(
      describeEntry(
        entry("finding.false_alarm", { reason: "our load test", returns_minutes: false }, "Asha"),
      ),
    ).toBe("Asha marked a finding a false alarm: “our load test”");
  });

  it("names the service a snooze or a mute was for", () => {
    expect(
      describeEntry(
        entry("service.snoozed", { service: "checkout", until: "2026-10-11T11:00:00Z" }, "Asha"),
      ),
    ).toBe("Asha snoozed checkout");
    expect(describeEntry(entry("service.muted", { service: "checkout", until: null }, "Raj"))).toBe(
      "Raj unmuted checkout",
    );
  });

  it("says nexovue adopted a new normal by itself", () => {
    const adopted: AuditEntry = {
      ...entry("finding.adopted", { shifts: [{ signal: "slower", ratio: 1.6 }] }, ""),
      actor: { kind: "system", id: null, name: null },
    };

    expect(describeEntry(adopted)).toBe(
      "nexovue made a finding's level the new normal after 7 days",
    );
  });
});

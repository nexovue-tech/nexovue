import { describe, expect, it } from "vitest";

import type { ApiKeySummary } from "./api";
import {
  describeTraffic,
  IN_USE_WITHIN_MS,
  isVerifiedQuiet,
  keyStatus,
  liveKeyCount,
  orderedKeys,
  QUIET_ENOUGH_TO_REVOKE_MS,
  suggestedRotationName,
} from "./key-status";

const NOW = Date.parse("2026-08-31T12:00:00Z");

function key(overrides: Partial<ApiKeySummary> = {}): ApiKeySummary {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    project_id: "22222222-2222-4222-8222-222222222222",
    name: "production exporter",
    key_hint: "nvk_live_…a4f2",
    created_at: "2026-08-01T09:00:00Z",
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

function agoBy(ms: number): string {
  return new Date(NOW - ms).toISOString();
}

describe("keyStatus", () => {
  it("calls a key nothing has authenticated with never used", () => {
    expect(keyStatus(key({ last_used_at: null }), NOW).traffic).toBe("never-used");
  });

  it("calls a key seen within the hour in use", () => {
    const status = keyStatus(key({ last_used_at: agoBy(IN_USE_WITHIN_MS - 1) }), NOW);
    expect(status.traffic).toBe("in-use");
  });

  it("stops calling a key in use the moment the hour is up", () => {
    expect(keyStatus(key({ last_used_at: agoBy(IN_USE_WITHIN_MS) }), NOW).traffic).toBe("settling");
  });

  it("will not call a key quiet until a full day of silence", () => {
    const almost = keyStatus(key({ last_used_at: agoBy(QUIET_ENOUGH_TO_REVOKE_MS - 1) }), NOW);
    const enough = keyStatus(key({ last_used_at: agoBy(QUIET_ENOUGH_TO_REVOKE_MS) }), NOW);

    expect(almost.traffic).toBe("settling");
    expect(enough.traffic).toBe("quiet");
  });

  it("reports how long a key has been silent", () => {
    const status = keyStatus(key({ last_used_at: agoBy(90 * 60_000) }), NOW);
    expect(status.silentForMs).toBe(90 * 60_000);
  });

  // A receiver's clock sitting ahead of the browser's must not read as a use in
  // the future, which would make a busy key look like it had been silent forever.
  it("treats a timestamp from the future as this instant", () => {
    const status = keyStatus(key({ last_used_at: new Date(NOW + 60_000).toISOString() }), NOW);

    expect(status.silentForMs).toBe(0);
    expect(status.traffic).toBe("in-use");
  });

  it("treats an unparseable timestamp as never used rather than as ancient", () => {
    expect(keyStatus(key({ last_used_at: "not a date" }), NOW).traffic).toBe("never-used");
  });

  it("carries the revoked flag independently of traffic", () => {
    const status = keyStatus(
      key({ last_used_at: agoBy(60_000), revoked_at: "2026-08-30T00:00:00Z" }),
      NOW,
    );

    expect(status.revoked).toBe(true);
    expect(status.traffic).toBe("in-use");
  });
});

describe("isVerifiedQuiet", () => {
  it("clears a key with a day of silence and one that was never used", () => {
    expect(
      isVerifiedQuiet(keyStatus(key({ last_used_at: agoBy(QUIET_ENOUGH_TO_REVOKE_MS) }), NOW)),
    ).toBe(true);
    expect(isVerifiedQuiet(keyStatus(key({ last_used_at: null }), NOW))).toBe(true);
  });

  it("refuses a key still in use, and one not quiet for long enough", () => {
    expect(isVerifiedQuiet(keyStatus(key({ last_used_at: agoBy(60_000) }), NOW))).toBe(false);
    expect(isVerifiedQuiet(keyStatus(key({ last_used_at: agoBy(2 * 60 * 60_000) }), NOW))).toBe(
      false,
    );
  });
});

describe("describeTraffic", () => {
  it("says what revoking an in-use key would do, in those words", () => {
    const sentence = describeTraffic(keyStatus(key({ last_used_at: agoBy(60_000) }), NOW));
    expect(sentence).toContain("stops that service sending telemetry");
  });

  it("describes a revoked key as withdrawn whatever its last traffic was", () => {
    const sentence = describeTraffic(
      keyStatus(key({ last_used_at: agoBy(60_000), revoked_at: "2026-08-30T00:00:00Z" }), NOW),
    );
    expect(sentence).toContain("Withdrawn");
  });

  it("has a sentence for every traffic state", () => {
    const cases = [
      null,
      agoBy(60_000),
      agoBy(2 * 60 * 60_000),
      agoBy(QUIET_ENOUGH_TO_REVOKE_MS + 1),
    ];
    for (const last_used_at of cases) {
      expect(describeTraffic(keyStatus(key({ last_used_at }), NOW))).not.toBe("");
    }
  });
});

describe("orderedKeys", () => {
  it("puts live keys above revoked ones, newest first within each group", () => {
    const ordered = orderedKeys([
      key({ id: "old-live", created_at: "2026-08-01T00:00:00Z" }),
      key({
        id: "revoked",
        created_at: "2026-08-20T00:00:00Z",
        revoked_at: "2026-08-21T00:00:00Z",
      }),
      key({ id: "new-live", created_at: "2026-08-30T00:00:00Z" }),
    ]);

    expect(ordered.map((each) => each.id)).toEqual(["new-live", "old-live", "revoked"]);
  });

  it("does not mutate what it was given", () => {
    const keys = [
      key({ id: "a", created_at: "2026-08-01T00:00:00Z" }),
      key({ id: "b", created_at: "2026-08-30T00:00:00Z" }),
    ];
    orderedKeys(keys);

    expect(keys.map((each) => each.id)).toEqual(["a", "b"]);
  });
});

describe("liveKeyCount", () => {
  it("counts only the keys that still authenticate", () => {
    expect(liveKeyCount([key(), key({ revoked_at: "2026-08-01T00:00:00Z" }), key()])).toBe(2);
  });
});

describe("suggestedRotationName", () => {
  it("keeps the old name and dates it, so a history stays readable", () => {
    expect(suggestedRotationName(key({ name: "production exporter" }), NOW)).toBe(
      "production exporter (rotated 2026-08-31)",
    );
  });
});

import { describe, expect, it } from "vitest";

import type { CheckResultSummary, CheckRun } from "./api";
import {
  certificateUrgency,
  checkUrgency,
  failedRunCount,
  formatCertificateAge,
  formatUptime,
  isPerfect,
  liveStateOf,
  runsOldestFirst,
  uptimeUrgency,
} from "./check-health";

/** The real check on the dev stack: 247 runs, 100% uptime, p95 1144ms, 59 days of cert. */
function summary(overrides: Partial<CheckResultSummary> = {}): CheckResultSummary {
  return {
    check_id: "6d3c2b1a-0000-4000-8000-000000000001",
    check_type: "http",
    runs: 247,
    successful_runs: 247,
    uptime_percent: 100,
    up: true,
    latest_status_code: 200,
    latest_run_at: "2026-08-30T10:01:00Z",
    mean_duration_ms: 812.4,
    p95_duration_ms: 1144,
    max_duration_ms: 1907.2,
    tls: { days_remaining: 59.4, issuer: "Let's Encrypt", server_address: "example.test" },
    ...overrides,
  };
}

describe("liveStateOf", () => {
  it("is unknown with no runs — absent is not down", () => {
    expect(liveStateOf(summary({ runs: 0, successful_runs: 0, up: null }))).toBe("unknown");
  });

  it("is unknown when the API reported no status, even with runs counted", () => {
    expect(liveStateOf(summary({ up: null }))).toBe("unknown");
  });

  it("reads up and down from the latest probe", () => {
    expect(liveStateOf(summary())).toBe("up");
    expect(liveStateOf(summary({ up: false }))).toBe("down");
  });
});

describe("uptimeUrgency", () => {
  it("gives a healthy check no colour at all", () => {
    expect(uptimeUrgency(summary())).toBe("none");
  });

  it("is critical while the check is down", () => {
    expect(uptimeUrgency(summary({ up: false, successful_runs: 240 }))).toBe("critical");
  });

  it("keeps a recovered outage visible as a warning", () => {
    expect(uptimeUrgency(summary({ successful_runs: 240, uptime_percent: 97.2 }))).toBe("warning");
  });

  it("says nothing about a check with no runs in the window", () => {
    expect(uptimeUrgency(summary({ runs: 0, successful_runs: 0, up: null }))).toBe("none");
  });
});

describe("certificateUrgency", () => {
  it("leaves a certificate inside its renewal cycle uncoloured", () => {
    expect(certificateUrgency(59.4)).toBe("none");
    expect(certificateUrgency(30)).toBe("none");
  });

  it("warns once a renewal window has been missed", () => {
    expect(certificateUrgency(21)).toBe("warning");
    expect(certificateUrgency(8)).toBe("warning");
  });

  it("is critical inside a week, and stays critical once expired", () => {
    expect(certificateUrgency(7)).toBe("critical");
    expect(certificateUrgency(0)).toBe("critical");
    expect(certificateUrgency(-3)).toBe("critical");
  });
});

describe("checkUrgency", () => {
  it("is none for the healthy check with a fresh certificate", () => {
    expect(checkUrgency(summary())).toBe("none");
  });

  it("takes the worst of uptime and certificate, not the first", () => {
    expect(
      checkUrgency(
        summary({ tls: { days_remaining: 3, issuer: "Let's Encrypt", server_address: "a.test" } }),
      ),
    ).toBe("critical");
  });

  it("ignores a certificate a plain-HTTP check does not have", () => {
    expect(checkUrgency(summary({ tls: null, successful_runs: 246 }))).toBe("warning");
  });
});

describe("formatUptime", () => {
  it("keeps the decimal, because 99.9% and 100% are different facts", () => {
    expect(formatUptime(summary())).toBe("100.0%");
    expect(formatUptime(summary({ uptime_percent: 99.9595 }))).toBe("100.0%");
    expect(formatUptime(summary({ uptime_percent: 97.24 }))).toBe("97.2%");
  });

  it("shows nothing rather than 0% when nothing ran", () => {
    expect(formatUptime(summary({ runs: 0, successful_runs: 0, up: null }))).toBe("—");
  });
});

describe("formatCertificateAge", () => {
  it("floors to whole days — half a certificate day is not actionable", () => {
    expect(formatCertificateAge(59.4)).toBe("59 days");
    expect(formatCertificateAge(1.9)).toBe("1 day");
  });

  it("names today and past-tense expiry rather than printing 0 days", () => {
    expect(formatCertificateAge(0.4)).toBe("expires today");
    expect(formatCertificateAge(-2)).toBe("expired");
  });
});

describe("failedRunCount and isPerfect", () => {
  it("counts what failed", () => {
    expect(failedRunCount(summary())).toBe(0);
    expect(failedRunCount(summary({ successful_runs: 240 }))).toBe(7);
  });

  it("never reports a negative count if the two totals disagree", () => {
    expect(failedRunCount(summary({ runs: 10, successful_runs: 12 }))).toBe(0);
  });

  it("calls a window perfect only when something actually ran in it", () => {
    expect(isPerfect(summary())).toBe(true);
    expect(isPerfect(summary({ runs: 0, successful_runs: 0, uptime_percent: 0 }))).toBe(false);
  });
});

describe("runsOldestFirst", () => {
  const run = (timestamp: string): CheckRun => ({
    timestamp,
    up: true,
    status_code: 200,
    duration_ms: 800,
    phase_total_ms: 795,
  });

  it("reverses the newest-first API order so a strip reads left to right as time", () => {
    const reversed = runsOldestFirst([run("10:02"), run("10:01"), run("10:00")]);
    expect(reversed.map((entry) => entry.timestamp)).toEqual(["10:00", "10:01", "10:02"]);
  });

  it("does not mutate what it was given", () => {
    const source = [run("10:02"), run("10:01")];
    runsOldestFirst(source);
    expect(source[0].timestamp).toBe("10:02");
  });
});

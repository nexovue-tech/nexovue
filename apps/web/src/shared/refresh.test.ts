// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RefreshPreference } from "./refresh";
import {
  declareStream,
  intervalById,
  NOTHING_LIVE,
  REFRESH_INTERVALS,
  REFRESH_STORAGE_KEY,
  readLiveStream,
  refetchIntervalFor,
  setPaused,
  setRefreshIntervalId,
} from "./refresh";

const LIVE: RefreshPreference = { paused: false, intervalId: "10s" };

afterEach(() => {
  setPaused(false);
  setRefreshIntervalId("10s");
  try {
    localStorage.removeItem(REFRESH_STORAGE_KEY);
  } catch {
    // jsdom without storage; the assertions that need it guard themselves.
  }
});

describe("what a live query should do", () => {
  it("polls at the chosen interval", () => {
    expect(refetchIntervalFor(LIVE, { windowIsAbsolute: false })).toBe(10_000);
    expect(refetchIntervalFor({ ...LIVE, intervalId: "1m" }, { windowIsAbsolute: false })).toBe(
      60_000,
    );
  });

  it("stops entirely when paused", () => {
    // `false`, not a large number: only one of the two actually stops.
    expect(refetchIntervalFor({ ...LIVE, paused: true }, { windowIsAbsolute: false })).toBe(false);
  });

  it("stops for an absolute window whatever the preference says", () => {
    // Nothing new can arrive in a period that closed last Tuesday. Polling it is a
    // request the customer pays for and nobody reads, so it is not overridable.
    expect(refetchIntervalFor(LIVE, { windowIsAbsolute: true })).toBe(false);
    expect(refetchIntervalFor({ ...LIVE, intervalId: "5s" }, { windowIsAbsolute: true })).toBe(
      false,
    );
  });
});

describe("the interval lookup", () => {
  it("falls back rather than throwing on a value it does not know", () => {
    // A remembered id from an older build must not leave the console unable to poll.
    expect(intervalById("никогда").ms).toBe(10_000);
  });

  it("offers nothing faster than five seconds", () => {
    // Below it a columnar read has not finished before the next one starts, and the
    // telemetry tier begins refusing reads the customer did nothing to deserve.
    expect(Math.min(...REFRESH_INTERVALS.map((option) => option.ms))).toBe(5_000);
  });
});

describe("what survives a reload", () => {
  beforeEach(() => {
    try {
      localStorage.removeItem(REFRESH_STORAGE_KEY);
    } catch {
      // See above.
    }
  });

  it("remembers the interval", () => {
    setRefreshIntervalId("5m");

    expect(localStorage.getItem(REFRESH_STORAGE_KEY)).toContain("5m");
  });

  it("never remembers a pause", () => {
    // A pause is a moment, not a preference. Somebody who paused to read a row
    // yesterday must not open the console tomorrow to numbers that never move and
    // no memory of why they stopped.
    setPaused(true);

    expect(localStorage.getItem(REFRESH_STORAGE_KEY) ?? "").not.toContain("paused");
  });
});

describe("what a screen says about its own liveness", () => {
  it("has no stream declared until a screen declares one", () => {
    declareStream(undefined);

    expect(readLiveStream()).toBeUndefined();
  });

  it("carries a stream's state and its controls to whatever renders them", () => {
    const pause = () => {};
    const resume = () => {};

    declareStream({ state: "paused", pause, resume });

    expect(readLiveStream()).toEqual({ state: "paused", pause, resume });
    declareStream(undefined);
  });

  it("distinguishes a screen with nothing live from one that has not said", () => {
    /* The two must not collapse: "no stream" is a polling screen, which shows an
       interval, and NOTHING_LIVE is a screen that polls nothing, which shows no
       control at all. Rendering an interval over a list of API keys is a control
       that governs nothing. */
    declareStream(NOTHING_LIVE);

    expect(readLiveStream()).toBe(NOTHING_LIVE);
    declareStream(undefined);
    expect(readLiveStream()).toBeUndefined();
  });
});

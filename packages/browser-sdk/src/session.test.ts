// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSession } from "./session";

const OPTIONS = {
  siteKey: "nvp_live_dZjiAYryiKTJ45KgB1EFUH",
  idleTimeoutMs: 30 * 60 * 1000,
  maxDurationMs: 4 * 60 * 60 * 1000,
};

const START = 1788626299167;

/** jsdom under Node 26 exposes no working localStorage, so the test supplies one. */
function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => [...values.keys()][index] ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  } as Storage;
}

beforeEach(() => vi.stubGlobal("localStorage", memoryStorage()));
afterEach(() => vi.unstubAllGlobals());

describe("session identity", () => {
  it("keeps one id across records inside the idle window", () => {
    const session = createSession(OPTIONS);
    const first = session.currentId(START);
    expect(session.currentId(START + 29 * 60 * 1000)).toBe(first);
  });

  it("rotates after the idle timeout", () => {
    const session = createSession(OPTIONS);
    const first = session.currentId(START);
    expect(session.currentId(START + 31 * 60 * 1000)).not.toBe(first);
  });

  it("rotates at the absolute cap, however busy the visitor was", () => {
    const session = createSession(OPTIONS);
    const first = session.currentId(START);
    let now = START;
    for (let step = 0; step < 60; step++) {
      now += 5 * 60 * 1000;
      session.currentId(now);
    }
    expect(session.currentId(now)).not.toBe(first);
  });

  it("survives a new tab, because the record is in localStorage", () => {
    const first = createSession(OPTIONS).currentId(START);
    expect(createSession(OPTIONS).currentId(START + 60_000)).toBe(first);
  });

  it("does not share a session between two site keys on one origin", () => {
    const other = { ...OPTIONS, siteKey: "nvp_live_AAAAAAAAAAAAAAAAAAAAAA" };
    expect(createSession(OPTIONS).currentId(START)).not.toBe(createSession(other).currentId(START));
  });

  it("treats a clock that moved backwards as expired rather than trusting it", () => {
    const session = createSession(OPTIONS);
    const first = session.currentId(START);
    expect(session.currentId(START - 60_000)).not.toBe(first);
  });

  it("ignores a stored record that is not a session", () => {
    localStorage.setItem(`nexovue.session.${OPTIONS.siteKey}`, '{"nonsense":true}');
    expect(createSession(OPTIONS).currentId(START)).toMatch(/^[0-9a-f]{32}$/);
  });
});

describe("when storage is unavailable", () => {
  it("still mints one stable id, which private mode and Safari's seven-day purge need", () => {
    vi.stubGlobal("localStorage", undefined);
    const session = createSession(OPTIONS);
    const first = session.currentId(START);
    expect(first).toMatch(/^[0-9a-f]{32}$/);
    expect(session.currentId(START + 1000)).toBe(first);
  });

  it("does not throw when every storage call raises", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("storage disabled");
      },
      setItem: () => {
        throw new Error("storage disabled");
      },
    });
    expect(() => createSession(OPTIONS).currentId(START)).not.toThrow();
  });
});

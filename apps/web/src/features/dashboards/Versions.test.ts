// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buttonNamed,
  DAY_MS,
  json,
  mountScreen,
  NOW,
  problem,
  settle,
  text,
  unmountAll,
} from "./render-harness.test-util";
import { Versions } from "./Versions";

/**
 * A dashboard's history (F20, T7.4, T7.7; AC56, AC120): who saved each version and when,
 * "Restore this version" opening one as a draft and writing nothing, "Remove at once" for
 * an owner or admin only, and a viewer who reads the list and is offered nothing. The
 * clock is pinned and passed in.
 */

const ID = "dash-1";
const at = (days: number) => new Date(NOW - days * DAY_MS).toISOString();
const HISTORY = {
  versions: [
    { version: 3, title: "Checkout on-call", saved_by: "Asha", saved_at: at(0.1), current: true },
    { version: 2, title: "Wrecked", saved_by: null, saved_at: at(2), current: false },
    { version: 1, title: "Checkout on-call", saved_by: "Ben", saved_at: at(9), current: false },
  ],
};
const VERSION_1 = {
  version: 1,
  title: "Checkout on-call",
  description: "The good one",
  definition: { schema_version: 1, panels: [] },
  saved_by: "Ben",
  saved_at: at(9),
  current: false,
};

function api(history: unknown = HISTORY) {
  return (request: { method: string; path: string }) => {
    if (request.path === `/dashboards/${ID}/versions`) return json(history);
    if (request.path === `/dashboards/${ID}/versions/1`) return json(VERSION_1);
    return undefined;
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

const screen = (onRestore?: (document: unknown) => void) =>
  createElement(Versions, { dashboardId: ID, title: "Checkout on-call", onRestore, now: NOW });

describe("the list", () => {
  it("shows each version with who saved it and when, the one in use marked", async () => {
    const { container } = await mountScreen(screen(vi.fn()), api());

    const rows = [...container.querySelectorAll("li")].map((each) => text(each));
    expect(rows[0]).toContain("Version 3");
    expect(rows[0]).toContain("Asha");
    expect(rows[0]).toContain("In use");
    expect(rows[1]).toContain("Version 2");
    expect(rows[1]).toContain("Former member");
    expect(rows[1]).toContain("titled Wrecked");
    expect(rows[2]).toContain("Ben");
    expect(rows[2]).not.toContain("titled");
  });

  it("says there are no earlier versions yet when only the one in use exists", async () => {
    const { container } = await mountScreen(
      screen(vi.fn()),
      api({ versions: [HISTORY.versions[0]] }),
    );
    expect(text(container)).toContain("No earlier versions yet.");
  });

  it("says it could not read the history, with a retry", async () => {
    const { container } = await mountScreen(screen(vi.fn()), () => problem(500, "Away."));
    expect(text(container)).toContain("Could not read the history");
    expect(buttonNamed(container, "Try again")).toBeDefined();
  });
});

describe("restoring a version (AC56)", () => {
  it("hands the version's document to the draft and writes nothing", async () => {
    const onRestore = vi.fn();
    const { container, calls } = await mountScreen(screen(onRestore), api());

    const restore = [...container.querySelectorAll("li")][2].querySelector("button");
    await act(async () => restore?.click());
    await settle();

    expect(onRestore).toHaveBeenCalledExactlyOnceWith({
      title: "Checkout on-call",
      description: "The good one",
      definition: VERSION_1.definition,
    });
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("is not offered for the version in use", async () => {
    const { container } = await mountScreen(screen(vi.fn()), api());
    const current = [...container.querySelectorAll("li")][0];
    expect(current.querySelector("button")).toBeNull();
  });
});

describe("who may", () => {
  it("offers a viewer the list and no button at all", async () => {
    const { container } = await mountScreen(screen(vi.fn()), api(), "viewer");
    expect(text(container)).toContain("Version 2");
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("offers a member Restore and no removal", async () => {
    const { container } = await mountScreen(screen(vi.fn()), api(), "member");
    expect(buttonNamed(container, "Restore this version")).toBeDefined();
    expect(buttonNamed(container, "Remove at once")).toBeUndefined();
  });

  it("lets an admin remove one version at once, after naming it, with one post (AC120)", async () => {
    const { container, calls } = await mountScreen(screen(vi.fn()), api(), "admin");

    act(() =>
      buttonNamed(
        [...container.querySelectorAll("li")][1] as HTMLElement,
        "Remove at once",
      )?.click(),
    );
    expect(text(container.querySelector("fieldset"))).toContain("version 2 of Wrecked");
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    await act(async () =>
      [...container.querySelectorAll("fieldset button")]
        .find((each) => text(each) === "Remove at once")
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true })),
    );
    await settle();

    expect(calls.filter((call) => call.method === "POST")).toEqual([
      { method: "POST", path: `/dashboards/${ID}/purge`, search: "", body: { version: 2 } },
    ]);
  });
});

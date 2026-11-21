// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardSummary } from "./api";
import { RecentlyDeleted } from "./RecentlyDeleted";
import {
  buttonNamed,
  DAY_MS,
  json,
  mountScreen,
  NOW,
  problem,
  settle,
  text,
  typeInto,
  unmountAll,
} from "./render-harness.test-util";

/**
 * Recently deleted (F20, T7.5; AC11, AC57, AC78): the list of what a delete hid, restoring
 * one whole, a title now in use asking for another, the cap said in the API's words, and a
 * viewer offered nothing that would fail. The clock is pinned and passed in.
 */

const gone = (title: string, days: number): DashboardSummary => ({
  id: `id-${title}`,
  title,
  description: "",
  owner: "Asha",
  panel_count: 4,
  uses_picker: false,
  panels_not_reading: 0,
  last_viewed_at: null,
  created_at: new Date(NOW - 90 * DAY_MS).toISOString(),
  updated_at: new Date(NOW - 90 * DAY_MS).toISOString(),
  deleted_at: new Date(NOW - days * DAY_MS).toISOString(),
});

const restored = { id: "id-Checkout", title: "Checkout" };

function api(deleted: DashboardSummary[], restore?: () => Response) {
  return (request: { method: string; path: string }) => {
    if (request.method === "GET" && request.path === "/dashboards") {
      return json({ dashboards: deleted, more: false });
    }
    if (request.method === "POST" && request.path.endsWith("/restore")) return restore?.();
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

const screen = () => createElement(RecentlyDeleted, { now: NOW });

describe("the list", () => {
  it("names each dashboard, when it was deleted and the days left to restore it", async () => {
    const { container } = await mountScreen(screen(), api([gone("Checkout", 2), gone("Old", 29)]));

    expect(text(container)).toContain("Checkout");
    expect(text(container)).toContain("can be restored for 28 more days");
    expect(text(container)).toContain("can be restored for 1 more day");
  });

  it("says nothing was deleted in the last 30 days when so", async () => {
    const { container } = await mountScreen(screen(), api([]));
    expect(text(container)).toContain("Nothing deleted in the last 30 days.");
  });

  it("says it could not read the list, with a retry", async () => {
    const { container } = await mountScreen(screen(), () => problem(500, "Away."));
    expect(text(container)).toContain("Could not read the list");
    expect(buttonNamed(container, "Try again")).toBeDefined();
  });
});

describe("restoring", () => {
  it("posts one restore with no title and says what came back", async () => {
    const { container, calls } = await mountScreen(
      screen(),
      api([gone("Checkout", 2)], () => json(restored)),
    );

    await act(async () => buttonNamed(container, "Restore")?.click());
    await settle();

    const posts = calls.filter((call) => call.method === "POST");
    expect(posts).toEqual([
      { method: "POST", path: "/dashboards/id-Checkout/restore", search: "", body: {} },
    ]);
    expect(text(container.querySelector('[role="status"]'))).toBe("Restored Checkout");
  });

  it("asks for another title when this one is in use, and restores under it (AC11)", async () => {
    let attempts = 0;
    const { container, calls } = await mountScreen(
      screen(),
      api([gone("Checkout", 2)], () => {
        attempts += 1;
        return attempts === 1
          ? problem(
              409,
              "A dashboard named 'Checkout' exists. Choose another title, or open that one.",
              {
                existing_dashboard_id: "holder",
              },
            )
          : json({ ...restored, title: "Checkout 2" });
      }),
    );

    await act(async () => buttonNamed(container, "Restore")?.click());
    await settle();
    expect(text(container)).toContain("A dashboard named 'Checkout' exists.");

    const field = container.querySelector<HTMLInputElement>('input[name="title"]');
    act(() => typeInto(field as HTMLInputElement, "Checkout 2"));
    await act(async () => buttonNamed(container, "Restore under this title")?.click());
    await settle();

    expect(calls.filter((call) => call.method === "POST").map((call) => call.body)).toEqual([
      {},
      { title: "Checkout 2" },
    ]);
    expect(text(container.querySelector('[role="status"]'))).toBe("Restored Checkout 2");
    // The question is answered: the form is gone.
    expect(container.querySelector('input[name="title"]')).toBeNull();
  });

  it("says the cap in the API's words and asks for no title (AC78)", async () => {
    const { container } = await mountScreen(
      screen(),
      api([gone("Checkout", 2)], () =>
        problem(409, "This organization holds 1,000 dashboards. Delete one first."),
      ),
    );

    await act(async () => buttonNamed(container, "Restore")?.click());
    await settle();

    expect(text(container)).toContain(
      "This organization holds 1,000 dashboards. Delete one first.",
    );
    expect(container.querySelector('input[name="title"]')).toBeNull();
  });
});

describe("who may", () => {
  it("offers a viewer the list and nothing to press", async () => {
    const { container } = await mountScreen(screen(), api([gone("Checkout", 2)]), "viewer");

    expect(text(container)).toContain("Checkout");
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("offers a member Restore but not Remove at once, and an admin both", async () => {
    const member = await mountScreen(screen(), api([gone("Checkout", 2)]), "member");
    expect(buttonNamed(member.container, "Restore")).toBeDefined();
    expect(buttonNamed(member.container, "Remove at once")).toBeUndefined();
    unmountAll();

    const admin = await mountScreen(screen(), api([gone("Checkout", 2)]), "admin");
    expect(buttonNamed(admin.container, "Remove at once")).toBeDefined();
  });

  it("removes a deleted dashboard at once only after the confirmation, once (AC120)", async () => {
    const { container, calls } = await mountScreen(screen(), api([gone("Checkout", 2)]), "owner");

    act(() => buttonNamed(container, "Remove at once")?.click());
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    await act(async () =>
      [...container.querySelectorAll("fieldset button")]
        .find((each) => text(each) === "Remove at once")
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true })),
    );
    await settle();

    expect(calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual([
      "/dashboards/id-Checkout/purge",
    ]);
  });
});

// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FixedIntervalRefresh } from "@/shared/refresh";
import { declareStream } from "@/shared/refresh";
import {
  clearScreenOverride,
  readSelection,
  setScreenOverride,
  setSelectedRangeId,
} from "@/shared/selection";

import { WindowControl } from "./WindowControl";

/**
 * While a dashboard is mounted, the top bar's window phrase and range picker are on the
 * project's clock, and the picker says so (F20, FR46, T6.7, AC64). The browser's zone here
 * is whatever the machine has; the project's is Kolkata, which differs from UTC all year.
 */

const FROM = "2026-11-10T02:00:00.000Z";
const TO = "2026-11-10T03:00:00.000Z";
let root: Root;
let container: HTMLElement;

const declaration: FixedIntervalRefresh = {
  kind: "fixed-interval",
  fixedWindow: false,
  savedIntervalMs: 60_000,
  intervalMs: 60_000,
  tabIntervalMs: undefined,
  chooseInterval: () => {},
  lastGoodAt: undefined,
  zone: "Asia/Kolkata",
  reading: false,
  readNow: () => {},
  chooseRange: vi.fn(),
};

async function show() {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  root = createRoot(container);
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient() },
        createElement(WindowControl),
      ),
    );
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-10T12:00:00.000Z"));
  container = document.createElement("div");
  document.body.append(container);
  setSelectedRangeId("1h");
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  clearScreenOverride();
  declareStream(undefined);
  vi.useRealTimers();
});

describe("the top bar while a dashboard is open", () => {
  it("writes a fixed window's phrase on the project's clock, and calls it 'Fixed time'", async () => {
    setScreenOverride({ zone: "Asia/Kolkata", window: { kind: "absolute", from: FROM, to: TO } });
    await show();
    expect(container.textContent).toContain("Fixed time");
    expect(container.textContent).toContain("07:30–08:30 on 10 Nov");
  });

  it("keeps the browser's clock and 'Window' on every other screen", async () => {
    setScreenOverride({ window: { kind: "absolute", from: FROM, to: TO } });
    await show();
    expect(container.textContent).toContain("Window");
    expect(container.textContent).not.toContain("Fixed time");
  });

  it("reads and writes the picker in the project's zone, and labels its fields (AC64)", async () => {
    setScreenOverride({ zone: "Asia/Kolkata" });
    await show();
    const open = [...container.querySelectorAll("button")].find(
      (b) => b.textContent === "Pick a range",
    );
    await act(async () => open?.click());
    const dialog = document.body.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("times in Kolkata time");
    const [from, to] = [...(dialog?.querySelectorAll("input") ?? [])];
    // The window on screen is the last hour ending at 12:00Z, which is 17:30 in Kolkata.
    expect(to?.value).toBe("2026-11-10T17:30");
    expect(from?.value).toBe("2026-11-10T16:30");
  });

  it("offers 30 days to a dashboard, chosen through the screen and never stored (FR52)", async () => {
    setScreenOverride({ zone: "Asia/Kolkata" });
    declareStream(declaration);
    await show();
    const select = container.querySelector("select");
    expect([...(select?.options ?? [])].map((o) => o.textContent)).toContain("30 days");
    await act(async () => {
      if (select) {
        select.value = "30d";
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    expect(declaration.chooseRange).toHaveBeenCalledWith("30d");
    expect(readSelection().window).toEqual({ kind: "relative", rangeId: "1h" });
  });

  it("does not offer 30 days elsewhere", async () => {
    await show();
    const select = container.querySelector("select");
    expect([...(select?.options ?? [])].map((o) => o.textContent)).not.toContain("30 days");
  });
});

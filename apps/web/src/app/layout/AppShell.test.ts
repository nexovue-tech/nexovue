// @vitest-environment jsdom

import { act, createElement, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubApi } from "@/features/assistant/panel.test-util";
import { openAssistant, resetAssistant } from "@/shared/assistant-context";
import { mount, unmountAll } from "@/shared/router-harness.test-util";

import { AppShell } from "./AppShell";

/**
 * F4 AC73: chat's panel sits beside the routed screen under its own error boundary. A panel
 * that throws while it renders leaves the screen mounted and usable.
 */

vi.mock("@/features/assistant/AssistantPanel", () => ({
  ASSISTANT_BUTTON_ID: "assistant-button",
  AssistantPanel: () => {
    throw new Error("the panel broke");
  },
}));

function Screen() {
  const [count, setCount] = useState(0);
  return createElement(
    "button",
    { type: "button", onClick: () => setCount(count + 1) },
    `Pressed ${count}`,
  );
}

afterEach(() => {
  unmountAll();
  resetAssistant();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a broken chat panel", () => {
  it("leaves the routed screen mounted and usable", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    // A wide window: the panel sits beside the screen rather than over it.
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("min-width"),
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    stubApi();
    openAssistant();

    const shell = await mount(Screen, AppShell);

    const button = shell.querySelector("main button") as HTMLButtonElement;
    expect(button.textContent).toBe("Pressed 0");
    await act(async () => button.click());
    expect(button.textContent).toBe("Pressed 1");
    expect(button.closest("[inert]")).toBeNull();
  });
});

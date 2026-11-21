// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { CopyButton } from "./CopyButton";

/**
 * A plain-HTTP install has no `navigator.clipboard` at all, so a copy fails; a caller
 * whose value is hidden is told, so it can show the value for selecting (F5 FR5a, AC11a's
 * screen rows). The clipboard is replaced; nothing else is.
 */

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

async function pressCopy(onUnavailable?: () => void): Promise<HTMLButtonElement> {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(createElement(CopyButton, { value: "the brief", label: "brief", onUnavailable })),
  );
  const button = container.querySelector("button");
  if (!button) throw new Error("no copy button");
  await act(async () => button.click());
  return button;
}

describe("CopyButton", () => {
  it("tells its caller when there is no clipboard at all", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    const unavailable = vi.fn();

    const button = await pressCopy(unavailable);

    expect(unavailable).toHaveBeenCalledTimes(1);
    expect(button.getAttribute("aria-label")).toBe("Copy brief");
  });

  it("tells its caller when the browser refuses the write", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("refused"));
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const unavailable = vi.fn();

    await pressCopy(unavailable);

    expect(writeText).toHaveBeenCalledWith("the brief");
    expect(unavailable).toHaveBeenCalledTimes(1);
  });

  it("calls nothing when the copy succeeds, and says it was copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const unavailable = vi.fn();

    const button = await pressCopy(unavailable);

    expect(unavailable).not.toHaveBeenCalled();
    expect(button.getAttribute("aria-label")).toBe("brief copied");
  });

  it("still works with no caller listening", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });

    const button = await pressCopy();

    expect(button.getAttribute("aria-label")).toBe("Copy brief");
  });
});

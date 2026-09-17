// @vitest-environment jsdom

import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement, useState } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { UnsavedChangesPrompt, useUnsavedChanges } from "./UnsavedChanges";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

/**
 * A form with one field, guarded exactly as the real ones are: dirty means the value
 * differs from what is stored, never that a field was touched.
 */
function GuardedForm({ stored, guarded = true }: { stored: string; guarded?: boolean }) {
  const [value, setValue] = useState(stored);
  const guard = useUnsavedChanges(guarded && value !== stored);

  return createElement(
    "div",
    null,
    createElement(UnsavedChangesPrompt, { guard, what: "the name" }),
    createElement("input", {
      "aria-label": "name",
      value,
      onChange: (event: { target: { value: string } }) => setValue(event.target.value),
    }),
    createElement(Link, { to: "/elsewhere" }, "Go elsewhere"),
  );
}

async function render(props: { stored: string; guarded?: boolean }) {
  const rootRoute = createRootRoute();
  const here = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => createElement(GuardedForm, props),
  });
  const elsewhere = createRoute({
    getParentRoute: () => rootRoute,
    path: "/elsewhere",
    component: () => createElement("p", null, "Somewhere else"),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([here, elsewhere]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));

  return {
    container,
    router,
    async type(next: string) {
      const input = container.querySelector("input");
      if (!input) throw new Error("the harness lost its input");
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      await act(async () => {
        setter?.call(input, next);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    async leave() {
      const link = container.querySelector("a");
      if (!link) throw new Error("the harness lost its link");
      await act(async () => link.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    },
    prompt: () => container.querySelector('[role="alertdialog"]'),
    button(label: string) {
      return [...container.querySelectorAll("button")].find(
        (candidate) => candidate.textContent === label,
      );
    },
  };
}

describe("AC19: leaving a form with unsaved changes", () => {
  it("lets an unchanged form be left without a word", async () => {
    const screen = await render({ stored: "checkout" });

    await screen.leave();

    expect(screen.prompt()).toBeNull();
    expect(screen.router.state.location.pathname).toBe("/elsewhere");
  });

  it("holds the navigation and says what is at risk", async () => {
    const screen = await render({ stored: "checkout" });
    await screen.type("checkout-api");

    await screen.leave();

    expect(screen.prompt()).not.toBeNull();
    expect(screen.prompt()?.textContent).toContain("the name");
    expect(screen.router.state.location.pathname).toBe("/");
  });

  it("stays put when the answer is to keep editing", async () => {
    const screen = await render({ stored: "checkout" });
    await screen.type("checkout-api");
    await screen.leave();

    await act(async () => screen.button("Keep editing")?.click());

    expect(screen.prompt()).toBeNull();
    expect(screen.router.state.location.pathname).toBe("/");
  });

  it("leaves when the answer is to discard", async () => {
    const screen = await render({ stored: "checkout" });
    await screen.type("checkout-api");
    await screen.leave();

    await act(async () => screen.button("Discard and leave")?.click());

    expect(screen.router.state.location.pathname).toBe("/elsewhere");
  });

  it("does not warn about a change that was typed and undone", async () => {
    // The difference between a warning people read and one they learn to click
    // away. Dirty means "differs from what is stored", never "was touched".
    const screen = await render({ stored: "checkout" });
    await screen.type("checkout-api");
    await screen.type("checkout");

    await screen.leave();

    expect(screen.prompt()).toBeNull();
    expect(screen.router.state.location.pathname).toBe("/elsewhere");
  });

  it("does not warn at all on a form that was not given a guard", async () => {
    // The unguarded forms submit immediately and have no draft to lose. Warning
    // there is the noise that costs the real warnings their meaning.
    const screen = await render({ stored: "checkout", guarded: false });
    await screen.type("checkout-api");

    await screen.leave();

    expect(screen.prompt()).toBeNull();
    expect(screen.router.state.location.pathname).toBe("/elsewhere");
  });
});

/* ------------------------------------------------------------------ keyboard */

/**
 * `role="alertdialog"` with `aria-modal="true"` tells a screen reader the rest of the
 * page is not there. The component said that and did none of it: focus stayed on the
 * link that was clicked, Escape did nothing, and Tab walked the page behind the scrim,
 * so a keyboard user met a screen that had silently stopped navigating.
 *
 * The tests above drive the guard through a real router; these drive the dialog alone,
 * because focus and key handling are the part a navigation test cannot see.
 */
describe("the prompt, from a keyboard", () => {
  let reset: ReturnType<typeof vi.fn>;
  let proceed: ReturnType<typeof vi.fn>;
  let container: HTMLElement;
  let root: Root;
  /** Whatever held focus when the dialog opened — the link that was clicked. */
  let outside: HTMLButtonElement;

  beforeEach(() => {
    reset = vi.fn();
    proceed = vi.fn();
    container = document.createElement("div");
    document.body.append(container);
    outside = document.createElement("button");
    outside.textContent = "Logs";
    document.body.append(outside);
    outside.focus();
    root = createRoot(container);
    mounted.push(root);
  });

  /** The shape `useBlocker({ withResolver: true })` hands back while it is blocking. */
  const blocked = () => ({ status: "blocked", reset, proceed }) as never;

  function open() {
    act(() => {
      root.render(createElement(UnsavedChangesPrompt, { guard: blocked(), what: "this check" }));
    });
    const dialog = container.querySelector<HTMLElement>('[role="alertdialog"]');
    if (!dialog) throw new Error("the prompt did not render");
    return dialog;
  }

  function press(key: string, options: { shiftKey?: boolean } = {}) {
    act(() => {
      document.activeElement?.dispatchEvent(
        new KeyboardEvent("keydown", { key, bubbles: true, ...options }),
      );
    });
  }

  it("takes focus, and puts it on staying rather than discarding", () => {
    const dialog = open();

    expect(document.activeElement?.closest('[role="alertdialog"]')).toBe(dialog);
    expect(document.activeElement?.textContent).toBe("Keep editing");
  });

  it("answers Escape by keeping the work, never by discarding it", () => {
    open();

    press("Escape");

    expect(reset).toHaveBeenCalledTimes(1);
    expect(proceed).not.toHaveBeenCalled();
  });

  it("still answers Escape once focus has left it, as a click on the scrim leaves it", () => {
    // The handler used to live on the dialog element, so a click anywhere outside —
    // the scrim is the whole viewport — left Escape inert and Tab walking the page
    // behind it. That is the reported defect, one click away from the fix.
    open();
    (document.body as HTMLElement).focus();
    outside.focus();

    press("Escape");

    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("keeps Tab inside itself, in both directions", () => {
    const dialog = open();
    const [first, last] = [...dialog.querySelectorAll("button")];

    last.focus();
    press("Tab");
    expect(document.activeElement).toBe(first);

    first.focus();
    press("Tab", { shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("pulls focus back when something outside takes it", () => {
    const dialog = open();

    act(() => {
      outside.focus();
      outside.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    });

    expect(document.activeElement?.closest('[role="alertdialog"]')).toBe(dialog);
  });

  it("gives focus back to where it came from when it closes", () => {
    open();
    expect(document.activeElement).not.toBe(outside);

    act(() => {
      root.render(
        createElement(UnsavedChangesPrompt, {
          guard: { status: "idle" } as never,
          what: "this check",
        }),
      );
    });

    expect(document.activeElement).toBe(outside);
  });
});

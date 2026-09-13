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
import { afterEach, beforeAll, describe, expect, it } from "vitest";

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

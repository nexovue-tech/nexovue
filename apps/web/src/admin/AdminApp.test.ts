// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { AdminApp } from "./AdminApp";

/**
 * The staff console's three places (F31): sign-in when no staff session answers, the
 * search when one does, and the onboarding form at its address — and, on an
 * organization, a support view of someone who has signed in, never of an invitation.
 */

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

function staffIs(signedIn: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      if (url.pathname === "/admin/v1/me") {
        return signedIn
          ? new Response(JSON.stringify({ id: "s1", email: "raj@nexovue.test", name: "Raj" }), {
              status: 200,
            })
          : new Response(
              JSON.stringify({ status: 401, title: "x", type: "x", detail: "Sign in." }),
              { status: 401 },
            );
      }
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
}

const ORG_ID = "00000000-0000-0000-0000-0000000000a1";
const PRIYA = "00000000-0000-0000-0000-0000000000b2";
// Opaque here: the console shows whatever link the admin API issued.
const TICKET_URL = "http://localhost:15173/support-link?ticket=t";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

/** Signed in, looking at Acme: Priya has joined, Sam is still invited. */
function viewingAcme(viewAs: (body: unknown) => void) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname === "/admin/v1/me")
        return json({ id: "s1", email: "r@n.test", name: "Raj" });
      if (url.pathname === `/admin/v1/organizations/${ORG_ID}/view-as`) {
        viewAs(JSON.parse(String(init?.body)));
        return json({ url: TICKET_URL });
      }
      if (url.pathname === `/admin/v1/organizations/${ORG_ID}`) {
        return json({
          organization: {
            id: ORG_ID,
            slug: "acme",
            name: "Acme",
            region: "india",
            plan: "pro",
            created_at: "2026-09-01T09:00:00Z",
            deleted: false,
            suspended: false,
          },
          suspension: null,
          plan_grant: null,
          members: [
            {
              id: PRIYA,
              email: "priya@acme.test",
              name: "Priya",
              role: "member",
              status: "active",
            },
            { id: "s-sam", email: "sam@acme.test", name: null, role: "viewer", status: "invited" },
          ],
          projects: [],
          usage: null,
        });
      }
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function settle() {
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount() {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client }, createElement(AdminApp)));
  });
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return container;
}

describe("the staff console", () => {
  it("asks a stranger for address, password and code", async () => {
    staffIs(false);
    const container = await mount();

    expect(container.textContent).toContain("Staff sign-in");
    expect(container.querySelector('input[name="code"]')).not.toBeNull();
  });

  it("opens on the search for a signed-in staff member", async () => {
    staffIs(true);
    const container = await mount();

    expect(container.textContent).toContain("Organizations");
    expect(container.textContent).toContain("Raj");
  });

  it("offers onboarding at its own address", async () => {
    staffIs(true);
    window.location.hash = "#/onboard";
    const container = await mount();

    expect(container.textContent).toContain("Onboard a paying customer");
    expect(container.querySelector('input[name="payment-id"]')).not.toBeNull();
  });

  it("views as someone who has joined, with a reason, through a link that works once", async () => {
    const viewAs = vi.fn();
    viewingAcme(viewAs);
    window.location.hash = `#/org/${ORG_ID}`;
    const container = await mount();

    const members = container.querySelector<HTMLElement>('section[aria-label="Members"]');
    const offered = [...(members?.querySelectorAll("button") ?? [])].map((b) => b.textContent);
    expect(offered).toEqual(["View as"]);

    await act(async () => members?.querySelector("button")?.click());
    const form = container.querySelector<HTMLFormElement>('form[aria-label="View as Priya"]');
    const reason = form?.querySelector<HTMLInputElement>('input[name="reason"]');
    await act(async () => typeInto(reason as HTMLInputElement, "checking their alert rule"));
    await act(async () => form?.requestSubmit());
    await settle();

    expect(viewAs).toHaveBeenCalledWith({ user_id: PRIYA, reason: "checking their alert rule" });
    const link = container.querySelector<HTMLAnchorElement>(`a[href="${TICKET_URL}"]`);
    expect(link?.target).toBe("_blank");
    expect(link?.rel).toContain("noopener");
  });
});

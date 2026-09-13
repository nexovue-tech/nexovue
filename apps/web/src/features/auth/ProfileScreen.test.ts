// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ProfileScreen } from "./ProfileScreen";
import { parseLoginSearch, SIGNED_OUT_EVERYWHERE } from "./search";

/**
 * The defects these guard, both of them about the two session behaviours being opposite:
 *
 * 1. A password change posted without the current password. The API refuses it — the
 *    field is required and verified — but the refusal costs a request on the strict auth
 *    tier, and the screen already knows the field is empty.
 * 2. Sign-out-everywhere surfacing as an expired session. The 204 clears the cookie, so
 *    the very next request 401s. If the screen did not navigate itself, app/query-client
 *    would bounce the customer to /login as if something had gone wrong — the control
 *    working and the session lapsing would look identical.
 */

const ADDRESS = "nexovue@nexovue.com";
const PASSWORD_PATH = "/api/v1/profile/password";
const SIGN_OUT_PATH = "/api/v1/profile/sign-out-everywhere";
const SESSIONS_PATH = "/api/v1/profile/sessions";
const TWO_FACTOR_PATH = "/api/v1/profile/two-factor";

/**
 * What the API answers for the session list, reduced exactly as it reduces it: a
 * browser family and a network, never a raw User-Agent and never an exact address.
 * The second row is another device, which is the one a worried customer ends.
 */
const SESSIONS = [
  {
    id: "a".repeat(64),
    created_at: "2026-09-13T08:00:00.000Z",
    last_seen_at: "2026-09-14T09:30:00.000Z",
    client: "Chrome on macOS",
    network: "203.0.113.0/24",
    current: true,
  },
  {
    id: "b".repeat(64),
    created_at: "2026-09-01T22:10:00.000Z",
    last_seen_at: "2026-09-02T06:05:00.000Z",
    client: "Safari on iOS",
    network: "198.51.100.0/24",
    current: false,
  },
];

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const IDENTITY = {
  user: {
    id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001",
    email: ADDRESS,
    name: "Dev",
    role: "owner",
    email_verified_at: "2026-09-01T09:15:00Z",
  },
  organization: { id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0002", slug: "dev", name: "Dev Co" },
};

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/**
 * The profile writes answer 204 with no body, exactly as the API renders them.
 *
 * `secondFactor` is a parameter because the section has two shapes and only one can be
 * on screen: off, with a form that asks for the password, and on, with the form that
 * takes it away again.
 */
function stubApi(options: { secondFactorEnabled?: boolean } = {}) {
  const posted: string[] = [];
  const deleted: string[] = [];

  const fetchMock = vi.fn(async (url: URL, init?: RequestInit) => {
    if (url.pathname.startsWith(`${SESSIONS_PATH}/`) && init?.method === "DELETE") {
      deleted.push(url.pathname);
      return new Response(null, { status: 204 });
    }
    if (url.pathname === SESSIONS_PATH) return json(SESSIONS, 200);
    if (url.pathname === TWO_FACTOR_PATH) {
      return json(
        {
          enabled: options.secondFactorEnabled ?? false,
          confirmed_at: options.secondFactorEnabled ? "2026-09-10T12:00:00.000Z" : null,
          recovery_codes_remaining: options.secondFactorEnabled ? 9 : 0,
        },
        200,
      );
    }
    if (url.pathname === `${TWO_FACTOR_PATH}/enrol`) {
      posted.push(url.pathname);
      return json(
        {
          secret: "JBSWY3DPEHPK3PXP",
          provisioning_uri: "otpauth://totp/nexovue:dev?secret=JBSWY3DPEHPK3PXP&issuer=nexovue",
        },
        200,
      );
    }
    if (url.pathname === `${TWO_FACTOR_PATH}/confirm`) {
      posted.push(url.pathname);
      return json({ recovery_codes: ["ABCDE-FGHJK", "MNPQR-STVWX"] }, 200);
    }
    if (url.pathname === PASSWORD_PATH || url.pathname === SIGN_OUT_PATH) {
      posted.push(url.pathname);
      return new Response(null, { status: 204 });
    }
    if (url.pathname.endsWith("/auth/me")) return json(IDENTITY, 200);
    throw new Error(`no stub for ${url.pathname}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return { fetch: fetchMock, posted, deleted };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** The screen plus the one route it can send somebody to. */
async function mountProfile() {
  const container = document.createElement("div");
  document.body.append(container);

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  const rootRoute = createRootRoute();
  const profileRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings/profile",
    component: () => createElement(QueryClientProvider, { client }, createElement(ProfileScreen)),
  });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    component: () => createElement("p", null, "Sign in"),
    validateSearch: parseLoginSearch,
  });

  const router = createRouter({
    routeTree: rootRoute.addChildren([profileRoute, loginRoute]),
    history: createMemoryHistory({ initialEntries: ["/settings/profile"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();

  return { container, router };
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function field(container: HTMLElement, name: string): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>(`input[name="${name}"]`);
  if (!input) throw new Error(`no ${name} field on the screen`);
  return input;
}

function buttonLabelled(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

async function click(button: HTMLButtonElement) {
  await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
}

async function submitPasswordForm(container: HTMLElement) {
  const form = field(container, "new_password").closest("form");
  if (!form) throw new Error("the password fields are not in a form");
  await act(async () => form.requestSubmit());
  await settle();
}

describe("the password form", () => {
  it("refuses to submit without the current password, and says which field", async () => {
    const api = stubApi();
    const { container } = await mountProfile();

    await act(async () => typeInto(field(container, "new_password"), "a-long-enough-one"));
    await submitPasswordForm(container);

    expect(api.posted).toEqual([]);
    expect(container.textContent).toContain("Enter the password on the account now.");
  });

  it("posts both passwords under the API's own field names once it has them", async () => {
    const api = stubApi();
    const { container } = await mountProfile();

    await act(async () => typeInto(field(container, "current_password"), "nexovue-dev"));
    await act(async () => typeInto(field(container, "new_password"), "a-long-enough-one"));
    await submitPasswordForm(container);

    expect(api.posted).toEqual([PASSWORD_PATH]);
    const calls = api.fetch.mock.calls as unknown as Array<[URL, RequestInit]>;
    const [, init] = calls.find(([url]) => url.pathname === PASSWORD_PATH) ?? [];
    expect(JSON.parse(String(init?.body))).toEqual({
      current_password: "nexovue-dev",
      new_password: "a-long-enough-one",
    });
  });

  it("says this session survives and the others do not, before and after the change", async () => {
    stubApi();
    const { container } = await mountProfile();

    // Stated before they commit — a control that surprises you when it fires is one you
    // stop trusting.
    expect(container.textContent).toContain("You stay signed in here");

    await act(async () => typeInto(field(container, "current_password"), "nexovue-dev"));
    await act(async () => typeInto(field(container, "new_password"), "a-long-enough-one"));
    await submitPasswordForm(container);

    expect(container.textContent).toContain(
      "every other session on this account has been signed out",
    );
    expect(container.textContent).toContain("You are still signed in here");
  });
});

describe("sign out everywhere", () => {
  it("goes to the sign-in screen with the notice that says the control worked", async () => {
    const api = stubApi();
    const { container, router } = await mountProfile();

    await click(buttonLabelled(container, "Sign out everywhere"));
    await click(buttonLabelled(container, "Yes, sign me out everywhere"));

    expect(api.posted).toEqual([SIGN_OUT_PATH]);
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toEqual({ notice: SIGNED_OUT_EVERYWHERE });
  });

  it("surfaces no failure on the way out — being signed out is the point of it", async () => {
    stubApi();
    const { container } = await mountProfile();

    await click(buttonLabelled(container, "Sign out everywhere"));
    await click(buttonLabelled(container, "Yes, sign me out everywhere"));

    expect(container.textContent).not.toContain("could not");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("says this session ends too, before it is confirmed", async () => {
    stubApi();
    const { container } = await mountProfile();

    expect(container.textContent).toContain("including this one");

    await click(buttonLabelled(container, "Sign out everywhere"));
    expect(container.textContent).toContain("You will land on the sign-in screen");
  });
});

describe("the signed-in devices list", () => {
  it("names a device coarsely and never the browser string or the exact address", async () => {
    stubApi();
    const { container } = await mountProfile();
    await settle();

    expect(container.textContent).toContain("Chrome on macOS");
    expect(container.textContent).toContain("203.0.113.0/24");
    // The two leaks the API's reduction exists to prevent, asserted where a future
    // change would first show them.
    expect(container.textContent).not.toContain("Mozilla");
    expect(container.textContent).not.toContain("203.0.113.42");
  });

  it("marks the session the reader is sitting in", async () => {
    stubApi();
    const { container } = await mountProfile();
    await settle();

    expect(container.textContent).toContain("This device");
  });

  it("ends one session by its own id rather than everything at once", async () => {
    const api = stubApi();
    const { container } = await mountProfile();
    await settle();

    await click(buttonLabelled(container, "End session"));

    expect(api.deleted).toEqual([`${SESSIONS_PATH}/${"b".repeat(64)}`]);
    expect(api.posted).toEqual([]);
  });
});

describe("two-factor authentication", () => {
  it("asks for the current password before it will even offer a secret", async () => {
    const api = stubApi();
    const { container } = await mountProfile();
    await settle();

    expect(api.posted).toEqual([]);
    expect(container.textContent).toContain("Set up two-factor authentication");
    expect(container.textContent).toContain("lock you out");
  });

  it("shows the secret only after the password, and arms nothing until a code", async () => {
    const api = stubApi();
    const { container } = await mountProfile();
    await settle();

    const enrolForm = buttonLabelled(container, "Set up two-factor authentication").closest("form");
    if (!enrolForm) throw new Error("the enrolment button is not in a form");
    await act(async () =>
      typeInto(
        enrolForm.querySelector<HTMLInputElement>(
          'input[name="current_password"]',
        ) as HTMLInputElement,
        "nexovue-dev",
      ),
    );
    await act(async () => enrolForm.requestSubmit());
    await settle();

    expect(container.textContent).toContain("JBSWY3DPEHPK3PXP");
    expect(container.textContent).toContain("Nothing changes on your account until that code");
    expect(api.posted).toEqual([`${TWO_FACTOR_PATH}/enrol`]);
  });

  it("shows the recovery codes once, saying they are shown nowhere else", async () => {
    stubApi();
    const { container } = await mountProfile();
    await settle();

    const enrolForm = buttonLabelled(container, "Set up two-factor authentication").closest("form");
    if (!enrolForm) throw new Error("the enrolment button is not in a form");
    await act(async () =>
      typeInto(
        enrolForm.querySelector<HTMLInputElement>(
          'input[name="current_password"]',
        ) as HTMLInputElement,
        "nexovue-dev",
      ),
    );
    await act(async () => enrolForm.requestSubmit());
    await settle();

    const confirmForm = field(container, "code").closest("form");
    if (!confirmForm) throw new Error("the code field is not in a form");
    await act(async () => typeInto(field(container, "code"), "123456"));
    await act(async () => confirmForm.requestSubmit());
    await settle();

    expect(container.textContent).toContain("ABCDE-FGHJK");
    expect(container.textContent).toContain("shown here and nowhere else");
  });

  it("offers to turn it off behind the password once it is on", async () => {
    stubApi({ secondFactorEnabled: true });
    const { container } = await mountProfile();
    await settle();

    expect(container.textContent).toContain("Turn off two-factor authentication");
    expect(container.textContent).toContain("9 of your recovery codes are unused");
    expect(container.textContent).not.toContain("Set up two-factor authentication");
  });
});

// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { VerifyEmailBanner } from "./VerifyEmailBanner";

/**
 * The defects these guard:
 *
 * 1. A banner that outlives verification. `email_verified_at` is the only thing that
 *    says the address is proven, so a screen that ignores it would keep offering a link
 *    to somebody who already clicked one.
 * 2. A resend that can be fired repeatedly. `/auth/verify-email/resend` matches the
 *    API's `/auth/verify-email` prefix in ratelimit/policy.py, which puts it on the
 *    strict auth tier — ten a minute. A customer tapping a live button while waiting for
 *    mail would spend that allowance on mail already in flight.
 */

const VERIFIED_AT = "2026-09-01T09:15:00Z";
const ADDRESS = "dev@nexovue.local";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

function identityBody(emailVerifiedAt: string | null) {
  return {
    user: {
      id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001",
      email: ADDRESS,
      name: "Dev",
      role: "owner",
      email_verified_at: emailVerifiedAt,
    },
    organization: { id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0002", slug: "dev", name: "Dev Co" },
  };
}

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

interface ApiStub {
  fetch: ReturnType<typeof vi.fn>;
  /** Paths of every resend that reached the wire. Length is the whole assertion. */
  resends: string[];
  /** Releases a held resend response. Only set when `holdResend` was asked for. */
  releaseResend?: () => void;
}

/** `/auth/me` answers with the identity given; resend answers 202 with a null body, as
    FastAPI renders it for an endpoint that returns nothing. */
function stubApi({
  emailVerifiedAt,
  holdResend = false,
}: {
  emailVerifiedAt: string | null;
  holdResend?: boolean;
}): ApiStub {
  const stub: ApiStub = { fetch: vi.fn(), resends: [] };

  let release: (() => void) | undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  if (holdResend) stub.releaseResend = release;

  stub.fetch = vi.fn(async (url: URL) => {
    if (url.pathname.endsWith("/auth/verify-email/resend")) {
      stub.resends.push(url.pathname);
      if (holdResend) await held;
      return json(null, 202);
    }
    if (url.pathname.endsWith("/auth/me")) return json(identityBody(emailVerifiedAt), 200);
    throw new Error(`no stub for ${url.pathname}`);
  });

  vi.stubGlobal("fetch", stub.fetch);
  return stub;
}

/** Lets queries, mutations and their re-renders finish. */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mountBanner(): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.append(container);

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const root = createRoot(container);
  mounted.push(root);

  await act(async () =>
    root.render(createElement(QueryClientProvider, { client }, createElement(VerifyEmailBanner))),
  );
  await settle();

  return container;
}

function resendButton(container: HTMLElement): HTMLButtonElement | null {
  return container.querySelector("button");
}

async function click(button: HTMLButtonElement) {
  await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
}

describe("the verify-email banner", () => {
  it("is not on screen at all once the address has been verified", async () => {
    stubApi({ emailVerifiedAt: VERIFIED_AT });
    const container = await mountBanner();

    expect(container.textContent).toBe("");
    expect(resendButton(container)).toBeNull();
  });

  it("offers a fresh link while the address is unproven", async () => {
    stubApi({ emailVerifiedAt: null });
    const container = await mountBanner();

    expect(container.textContent).toContain("is not verified yet");
    expect(container.textContent).toContain(ADDRESS);
    expect(resendButton(container)?.textContent).toBe("Send a new link");
  });

  it("reads as an offer rather than a warning", async () => {
    stubApi({ emailVerifiedAt: null });
    const container = await mountBanner();

    // Verification gates nothing in Phase 1, so nothing here may claim it does.
    expect(container.querySelector('[role="status"]')).not.toBeNull();
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.textContent).toContain("Nothing here needs it");
  });

  it("will not send a second link once one has been handed to the mail provider", async () => {
    const api = stubApi({ emailVerifiedAt: null });
    const container = await mountBanner();

    const button = resendButton(container);
    if (!button) throw new Error("the banner is not offering a link");
    await click(button);

    expect(api.resends).toHaveLength(1);
    // The button is gone, not merely disabled: there is nothing left to press.
    expect(resendButton(container)).toBeNull();
  });

  it("does not claim the mail was delivered, only handed over", async () => {
    stubApi({ emailVerifiedAt: null });
    const container = await mountBanner();

    const button = resendButton(container);
    if (!button) throw new Error("the banner is not offering a link");
    await click(button);

    // A 202 means a provider accepted it. "Sent" would be a claim the API never made.
    expect(container.textContent).toContain("handed it to our mail provider");
    expect(container.textContent).not.toContain("Sent!");
  });

  it("refuses a second press while the first request is still in flight", async () => {
    const api = stubApi({ emailVerifiedAt: null, holdResend: true });
    const container = await mountBanner();

    const button = resendButton(container);
    if (!button) throw new Error("the banner is not offering a link");
    await click(button);

    const inFlight = resendButton(container);
    expect(inFlight?.disabled).toBe(true);
    await click(inFlight as HTMLButtonElement);
    expect(api.resends).toHaveLength(1);

    api.releaseResend?.();
    await settle();
    expect(api.resends).toHaveLength(1);
  });
});

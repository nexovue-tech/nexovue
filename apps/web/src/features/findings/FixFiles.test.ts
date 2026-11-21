// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FindingSourceFix, SourceFix } from "./api";
import { SUGGESTED_FIX } from "./fix-page";
import {
  finding,
  json,
  mountFinding,
  problem,
  readable,
  settle,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * F5 rung 3 on the finding page (plan T3.12; spec FR13a, AC3b, AC37d's states, AC46). Only
 * the network is replaced and the clock fixed. A requester reads the list from the API's
 * own sentences, removes a file, sends bound to the digest, and cancels; a 409 shows the
 * API's sentence; a poll changes only the status line.
 */

const NOW = new Date("2026-11-21T12:00:00Z");

const source = (overrides: Partial<FindingSourceFix> = {}): FindingSourceFix => ({
  finding_id: "f-1",
  line: null,
  offer: null,
  latest: null,
  earlier: [],
  support_view_note: null,
  ...overrides,
});

const OFFER = source({
  offer: {
    action: "Ask for a suggested fix",
    result: "ready_without_stack",
    label: "Starts from the files you name.",
    name_files_lead: "Name up to 5 files you think are involved, as paths inside services/shop.",
    folder: "services/shop",
    tries_left: 3,
    needs_link: false,
    link_sentence: null,
  },
});

const GATHERED: SourceFix = {
  id: "s-1",
  state: "gathered",
  heading: "Files to send",
  sentence: null,
  reason: null,
  neutral: false,
  requested_at: "2026-11-21T11:55:00Z",
  state_changed_at: "2026-11-21T11:55:00Z",
  files: {
    files: [
      {
        ref: "file_1",
        path: "services/shop/app/handler.py",
        role: "stack",
        why: "named at line 41 of the error's stack",
        bytes: 120,
        lines_sent: null,
        moved_on_default: true,
        github_url: "https://github.com/acme/shop/blob/cccc/services/shop/app/handler.py",
      },
    ],
    left_out: [{ path: "services/shop/.env", why: "never sent: secrets and keys", bytes: 10 }],
    unmatched: 2,
    typed_missing: [],
    commit: "c".repeat(40),
    commit_known: false,
    commit_sentence: "The running version is not known, so this read the default branch.",
    top_left_out: false,
    digest: "d".repeat(64),
    bytes_sent: 120,
    provider_host: "provider.example",
    round: 1,
    lapses_at: "2026-11-21T12:10:00Z",
    forgeable:
      "These files came from the error's stack, which an outsider can forge. If one looks unrelated to this error, remove it.",
    send_host: "To provider.example:",
    send: "Send these files",
  },
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

async function page(rung3: FindingSourceFix, extra: Record<string, ReturnType<typeof json>> = {}) {
  const api = stubApi(finding(), {
    role: "member",
    extra: { "GET /findings/f-1/fixes/source": json(rung3), ...extra },
  });
  const { container } = await mountFinding();
  await waitFor(() => container.querySelector("h1"), "the finding's sentence");
  for (let round = 0; round < 6; round += 1) await settle();
  const section = container.querySelector<HTMLElement>(`#${SUGGESTED_FIX.anchor}`);
  return { api, container, section };
}

function click(element: Element | null | undefined) {
  act(() => {
    (element as HTMLElement).click();
  });
}

describe("rung 3's list of files (FR13a)", () => {
  it("lists each file with its reason, a GitHub link, what was left out and a count", async () => {
    const { section } = await page(source({ latest: GATHERED }));

    const text = readable(section as Element);
    expect(text).toContain("named at line 41 of the error's stack");
    expect(text).toContain("services/shop/.env: never sent: secrets and keys");
    expect(text).toContain("2 names from the error matched no file.");
    expect(text).toContain("This list waits 10 more minutes.");
    expect(text).toContain("This file has changed on the default branch");
    const link = section?.querySelector<HTMLAnchorElement>("a[href^='https://github.com']");
    expect(link?.textContent).toBe("services/shop/app/handler.py");
  });

  it("sends bound to the digest on screen, and shows the API's sentence back", async () => {
    const { api, section } = await page(source({ latest: GATHERED }), {
      "POST /findings/f-1/fixes/s-1/send": problem(
        409,
        "Sending files to the AI provider comes in a later version of Nexovue. Nothing was sent.",
      ),
    });

    const send = [...(section?.querySelectorAll("button") ?? [])].find(
      (button) => button.textContent === "Send these files",
    );
    click(send);
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find((call) => call.path === "/findings/f-1/fixes/s-1/send");
    expect(posted?.body).toMatchObject({ files_digest: "d".repeat(64) });
    expect(section?.querySelector('[role="alert"]')?.textContent).toContain("Nothing was sent.");
  });

  it("removing a file reads the list again instead of sending", async () => {
    const { api, section } = await page(source({ latest: GATHERED }));

    click(section?.querySelector("input[type='checkbox']"));
    const read = [...(section?.querySelectorAll("button") ?? [])].find(
      (button) => button.textContent === "Read the list again",
    );
    click(read);
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find((call) => call.path === "/findings/f-1/fixes/s-1/files");
    expect(posted?.body).toEqual({ remove: ["services/shop/app/handler.py"], add: [] });
  });

  it("a cancel reads neutrally, with Ask offered again (AC46)", async () => {
    const cancelled: SourceFix = {
      ...GATHERED,
      state: "failed",
      heading: "No suggestion",
      sentence: "You cancelled this. Nothing was sent.",
      reason: "cancelled",
      neutral: true,
      files: null,
    };
    const { section } = await page({ ...OFFER, latest: cancelled });

    const text = readable(section as Element);
    expect(text).toContain("You cancelled this. Nothing was sent.");
    expect(text).toContain("Ask for a suggested fix");
  });
});

describe("Ask (FR11)", () => {
  it("names files when there is no stack, and posts the typed paths", async () => {
    const { api, section } = await page(OFFER);

    const input = section?.querySelector<HTMLInputElement>("input[name='path-0']");
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "app/handler.py");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const ask = [...(section?.querySelectorAll("button") ?? [])].find(
      (button) => button.textContent === "Ask for a suggested fix",
    );
    click(ask);
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find(
      (call) => call.method === "POST" && call.path === "/findings/f-1/fixes",
    );
    expect(posted?.body).toEqual({ paths: ["app/handler.py"], notify: true });
  });

  it("asks an unlinked person to link first under the owner's requirement (D20)", async () => {
    const offer = OFFER.offer;
    if (offer === null) throw new Error("fixture");
    const { section } = await page(
      source({
        offer: {
          ...offer,
          needs_link: true,
          link_sentence: "Connect your GitHub account so GitHub can tell us what access you have.",
        },
      }),
    );

    const text = readable(section as Element);
    expect(text).toContain("Connect your GitHub account");
    expect(text).not.toContain("Ask for a suggested fix");
  });

  it("a viewer's read holds no path and no action", async () => {
    const viewer: SourceFix = {
      ...GATHERED,
      heading: "Reading files",
      sentence: "A suggested fix is being prepared.",
      files: null,
    };
    const { section } = await page(source({ latest: viewer }));

    expect(readable(section as Element)).toContain("A suggested fix is being prepared.");
    expect(section?.querySelector("button")).toBeNull();
  });
});

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
  proposal: null,
  code_withheld: null,
  check_action: null,
  check: null,
  file_count: null,
  line_count: null,
  files: {
    asked_round: null,
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
        "The file list changed. Check the new list, then send again. Nothing was sent.",
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

/* ------------------------------------------------------- Step 4: the suggestion */

const PROPOSED: SourceFix = {
  ...GATHERED,
  state: "proposed",
  heading: "Suggested fix",
  files: null,
  proposal: {
    lead: "Suggested fix: 1 file, 3 lines, in services/shop. Not compiled and not run.",
    starts_from: "Starts from the likely cause above.",
    facts_note: "Line numbers and context are from the file as read at ccccccc.",
    flags: [
      {
        kind: "new_import",
        label: "new import",
        path: "services/shop/app/h.py",
        line: 5,
        side: "new",
      },
    ],
    files: [
      {
        ref: "file_1",
        path: "services/shop/app/h.py",
        github_url: null,
        added: 1,
        removed: 2,
        open: true,
        context_note: null,
        hunks: [
          {
            header: "@@ -4,3 +4,2 @@",
            lines: [
              { marker: " ", text: "    amount = order.total", old: 4, new: 4, flags: [] },
              { marker: "-", text: "    if amount < 0:", old: 5, new: null, flags: [] },
              {
                marker: "+",
                text: "    import requests",
                old: null,
                new: 5,
                flags: ["new import"],
              },
            ],
          },
        ],
        explanations: [{ segments: [{ text: "checkout fails after a deploy." }] }],
      },
    ],
    explanation_label: "The model's explanation, not checked by Nexovue",
    not_checked: ["Not compiled and not run by Nexovue. A subtle bug passes every rule."],
    files_read: GATHERED.files?.files ?? [],
    left_out: [],
    written_at: "2026-11-21T11:00:00Z",
    commit: "ccccccc",
    tokens: 18204,
  },
};

describe("Step 4: a written suggestion (T4.6, AC10, AC37e)", () => {
  it("shows the lead, the flags, the diff as text, the explanation set apart, and tokens", async () => {
    const { section } = await page(source({ latest: PROPOSED }));

    const text = readable(section as Element);
    expect(text).toContain("Suggested fix: 1 file, 3 lines, in services/shop.");
    expect(text).toContain("new import: services/shop/app/h.py, line 5");
    expect(section?.querySelector("ins")?.textContent).toBe("    import requests");
    expect(section?.querySelector("del")?.textContent).toBe("    if amount < 0:");
    expect(text).toContain("The model's explanation, not checked by Nexovue");
    expect(text).toContain("18,204 tokens used.");
    expect(document.title).toBe("Suggestion ready");
    const jump = section?.querySelector<HTMLAnchorElement>("a[href^='#fix-line-new-5']");
    expect(jump).not.toBeNull();
    expect(section?.querySelector(`[id='${jump?.getAttribute("href")?.slice(1)}']`)).not.toBeNull();
  });

  it("a viewer reads counts and the sentence, never code", async () => {
    const viewer: SourceFix = {
      ...PROPOSED,
      proposal: null,
      sentence: "Code is shown to people who can ask for a suggested fix.",
      file_count: 1,
      line_count: 3,
    };
    const { section } = await page(source({ latest: viewer }));

    expect(readable(section as Element)).toContain("Code is shown to people who can ask");
    expect(section?.querySelector("ins")).toBeNull();
  });

  it("under the owner's requirement, Check my access posts a read check (D20)", async () => {
    const withheld: SourceFix = {
      ...PROPOSED,
      proposal: null,
      code_withheld:
        "The owner limits quoted code to people with access to this repository on GitHub.",
      check_action: "Check my access",
    };
    const { api, section } = await page(source({ latest: withheld }), {
      "POST /findings/f-1/fixes/s-1/check": json({
        id: "c-1",
        purpose: "read",
        status: "running",
        heading: "Checking your access",
        sentence: null,
        passed: false,
        expires_at: null,
      }),
    });

    expect(section?.querySelector("ins")).toBeNull();
    const button = [...(section?.querySelectorAll("button") ?? [])].find(
      (each) => each.textContent === "Check my access",
    );
    click(button);
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find((call) => call.path === "/findings/f-1/fixes/s-1/check");
    expect(posted?.body).toEqual({ for: "read" });
  });

  it("after the AI asks, the round shows each file, why, and its words labelled (AC88)", async () => {
    const files = GATHERED.files;
    if (files === null) throw new Error("fixture");
    const asked: SourceFix = {
      ...GATHERED,
      files: {
        ...files,
        round: 2,
        asked_round: {
          lead: "The AI asks for 1 more files. Send them?",
          round_sentence: "Round 2 of at most 3.",
          model_words_label: "The model's words, not checked by Nexovue",
          asked: [
            {
              path: "services/shop/app/tax.py",
              because: "asked because of services/shop/app/handler.py, line 41",
              model_words: "total calls the tax rules there.",
            },
          ],
          refused: [{ path: "services/shop/.env", why: "never sent: secrets and keys" }],
          unmatched: 1,
        },
      },
    };
    const { section } = await page(source({ latest: asked }));

    const text = readable(section as Element);
    expect(text).toContain("The AI asks for 1 more files. Send them?");
    expect(text).toContain("asked because of services/shop/app/handler.py, line 41");
    expect(text).toContain(
      "The model's words, not checked by Nexovue: total calls the tax rules there.",
    );
    expect(text).toContain("services/shop/.env: never sent: secrets and keys");
    expect(text).toContain("Round 2 of at most 3.");
  });
});

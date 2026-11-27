// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatTimestamp } from "@/shared/format/time";
import type { FindingSourceFix, SourceFix } from "./api";
import { sourceFixWaiting } from "./api";
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
  patch_action: null,
  patch: null,
  verdict: null,
  draft: null,
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
    send_host: "To provider.example, using up to about 24,082 tokens.",
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

  it("shows no forgery warning when no file came from a stack (live test E3)", async () => {
    const empties = async (latest: SourceFix) => {
      const { section } = await page(source({ latest }));
      const found = section?.querySelectorAll("p:empty").length;
      const text = readable(section as Element);
      unmountAll();
      return { found, text };
    };
    const stacked = await empties(GATHERED);
    const named = await empties({ ...GATHERED, files: { ...GATHERED.files!, forgeable: null } });

    expect(stacked.text).toContain("an outsider can forge");
    expect(named.text).not.toContain("an outsider can forge");
    // No empty paragraph left where the warning was.
    expect(named.found).toBe(stacked.found);
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
        moved_note: null,
      },
    ],
    explanation_label: "The model's explanation, not checked by Nexovue",
    not_checked: ["Not compiled and not run by Nexovue. A subtle bug passes every rule."],
    files_read: GATHERED.files?.files ?? [],
    left_out: [],
    written_at: "2026-11-21T11:00:00Z",
    commit: "ccccccc",
    tokens: 18204,
    rebuilt_note: null,
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

describe("Step 5: the patch and the verdict (T5.2, FR20a, FR21, FR29)", () => {
  const ready: SourceFix = {
    ...PROPOSED,
    patch_action: "Prepare patch",
    patch: {
      status: "moved",
      heading: "Ready to confirm",
      sentence:
        "Built against ddddddd, the default branch's head when it was re-read. Apply it with git apply. It can be downloaded until 12:15 UTC; prepare it again after that.",
      downloadable: true,
      expires_at: "2026-11-21T12:15:00Z",
    },
    verdict: {
      question: "Was this suggestion useful?",
      answers: [
        { value: "merge_as_is", label: "Yes, nearly as it is" },
        { value: "useful_start", label: "Yes, as a start" },
        { value: "wrong", label: "No, it was wrong" },
      ],
      chosen: null,
      recorded: null,
    },
    proposal: PROPOSED.proposal && {
      ...PROPOSED.proposal,
      rebuilt_note:
        "A file moved on since the suggestion, and every change still applies. The changes below are re-read from the default branch's head.",
      files: PROPOSED.proposal.files.map((file) => ({
        ...file,
        moved_note: "This file moved on since the suggestion.",
      })),
    },
  };

  it("Prepare patch posts a patch check, and a standing check downloads from the person's own link", async () => {
    const { api, section } = await page(source({ latest: ready }), {
      "POST /findings/f-1/fixes/s-1/check": json({
        id: "c-2",
        purpose: "patch",
        status: "running",
        heading: "Re-reading the files",
        sentence: null,
        passed: false,
        expires_at: null,
      }),
    });

    const text = readable(section as Element);
    expect(text).toContain("Ready to confirm");
    expect(text).toContain("This file moved on since the suggestion.");
    const link = section?.querySelector<HTMLAnchorElement>("a[download]");
    expect(link?.getAttribute("href")).toBe("/api/v1/findings/f-1/fixes/s-1/patch");
    expect(link?.textContent).toBe("Download patch");
    const button = [...(section?.querySelectorAll("button") ?? [])].find(
      (each) => each.textContent === "Prepare patch",
    );
    click(button);
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find((call) => call.path === "/findings/f-1/fixes/s-1/check");
    expect(posted?.body).toEqual({ for: "patch" });
  });

  it("a check still re-reading shows its sentence, no download, and no second Prepare", async () => {
    const working: SourceFix = {
      ...ready,
      patch_action: null,
      patch: {
        status: "running",
        heading: "Re-reading the files",
        sentence: "Re-reading the files and re-applying Nexovue's rules.",
        downloadable: false,
        expires_at: null,
      },
    };
    const { section } = await page(source({ latest: working }));

    expect(readable(section as Element)).toContain("Re-reading the files and re-applying");
    expect(section?.querySelector("a[download]")).toBeNull();
    expect(
      [...(section?.querySelectorAll("button") ?? [])].some(
        (each) => each.textContent === "Prepare patch",
      ),
    ).toBe(false);
  });

  it("answering the question posts the verdict and shows that it changes nothing else", async () => {
    const { api, section } = await page(source({ latest: ready }), {
      "POST /findings/f-1/fixes/s-1/verdict": json({
        ...ready.verdict,
        chosen: "useful_start",
        recorded: "Recorded. This changes nothing else.",
      }),
    });

    const answer = [
      ...(section?.querySelectorAll<HTMLInputElement>("input[type=radio]") ?? []),
    ].find((each) => each.value === "useful_start");
    click(answer);
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find((call) => call.path === "/findings/f-1/fixes/s-1/verdict");
    expect(posted?.body).toEqual({ verdict: "useful_start" });
    await waitFor(
      () => readable(section as Element).includes("Recorded. This changes nothing else."),
      "the recorded line",
    );
  });
});

describe("Step 7: the draft pull request (T7.8; FR10c, FR22, FR24, FR24a, FR26)", () => {
  const WRITES_OFF =
    "Opening a draft pull request is switched off in this version of Nexovue, until its safety evaluation is done. Download the patch instead.";
  const draft = (overrides: Partial<NonNullable<SourceFix["draft"]>> = {}) => ({
    action: null,
    refusal: null,
    needs_link: false,
    link_sentence: null,
    check: null,
    confirm: null,
    failure: null,
    pull_request_state: null,
    pull_request_url: null,
    pull_request_sentence: null,
    ci_note: null,
    delete_branch_action: null,
    ci_action: null,
    ci_confirm: null,
    ci_confirm_action: null,
    ci_status: null,
    merged_facts: null,
    ...overrides,
  });
  const CONFIRM = {
    repository: "acme/shop",
    branch: "nexovue/suggestion-s-1",
    file_count: 1,
    line_count: 2,
    facts: "acme/shop, branch nexovue/suggestion-s-1: 1 file, 2 lines.",
    ci_mode:
      "Asks GitHub Actions to hold `push` and `pull_request` runs. Other triggers, other CI systems and apps connected to this repository may still run.",
    warnings: [
      "This also starts a case on this finding.",
      "On GitHub this pull request's author is the App, so the person who requested it can approve it. We have not yet shown that a GitHub rule can stop that.",
      "Your GitHub login is written into the pull request, and stays on GitHub if you leave.",
      "Write access reaches every repository you installed this App on; Nexovue restricts where drafts open.",
    ],
    digest: "a".repeat(64),
    public: false,
    note_label: "Why this change (optional, one line)",
    note_public_confirm: null,
    action: "Open the draft",
    expires_at: "2026-11-21T12:14:00Z",
  };
  const buttons = (section: Element | null) => [...(section?.querySelectorAll("button") ?? [])];

  it("while write access is off, shows only the API's sentence and no draft button", async () => {
    const { section } = await page(
      source({
        latest: {
          ...PROPOSED,
          patch_action: "Prepare patch",
          draft: draft({ refusal: WRITES_OFF }),
        },
      }),
    );

    expect(readable(section as Element)).toContain(WRITES_OFF);
    expect(buttons(section).some((each) => each.textContent === "Open draft pull request")).toBe(
      false,
    );
    expect(buttons(section).some((each) => each.textContent === "Prepare patch")).toBe(true);
  });

  it("Open draft pull request asks this person's own draft check", async () => {
    const { api, section } = await page(
      source({ latest: { ...PROPOSED, draft: draft({ action: "Open draft pull request" }) } }),
      {
        "POST /findings/f-1/fixes/s-1/check": json({
          id: "c-3",
          purpose: "draft",
          status: "running",
          heading: "Re-reading the files",
          sentence: null,
          passed: false,
          expires_at: null,
        }),
      },
    );

    click(buttons(section).find((each) => each.textContent === "Open draft pull request"));
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find((call) => call.path === "/findings/f-1/fixes/s-1/check");
    expect(posted?.body).toEqual({ for: "draft" });
  });

  it("with no linked account, the button opens the link step and asks no check", async () => {
    const { api, section } = await page(
      source({
        latest: {
          ...PROPOSED,
          draft: draft({
            action: "Open draft pull request",
            needs_link: true,
            link_sentence:
              "Connect your GitHub account so GitHub can tell us what access you have.",
          }),
        },
      }),
    );

    click(buttons(section).find((each) => each.textContent === "Open draft pull request"));
    await settle();

    expect(readable(section as Element)).toContain("Connect your GitHub account so GitHub");
    expect(api.calls.some((call) => call.path.endsWith("/check"))).toBe(false);
  });

  it("FR24: the confirm states every fact and warning by exact string and posts the digest and note", async () => {
    const { api, section } = await page(
      source({ latest: { ...PROPOSED, draft: draft({ confirm: CONFIRM }) } }),
      {
        "POST /findings/f-1/fixes/s-1/draft-pull-request": json({
          fix_id: "s-1",
          state: "approved",
        }),
      },
    );

    const text = readable(section as Element);
    expect(text).toContain(CONFIRM.facts);
    expect(text).toContain(CONFIRM.ci_mode);
    for (const warning of CONFIRM.warnings) expect(text).toContain(warning);
    const note = section?.querySelector<HTMLInputElement>("input[name=requester-note]");
    act(() => {
      if (note) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
        setter?.call(note, "Retries stop the timeouts.");
        note.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    click(buttons(section).find((each) => each.textContent === "Open the draft"));
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find(
      (call) => call.path === "/findings/f-1/fixes/s-1/draft-pull-request",
    );
    expect(posted?.body).toEqual({
      digest: "a".repeat(64),
      note: "Retries stop the timeouts.",
      note_confirmed: false,
    });
  });

  it("FR24a: a public repository's confirm asks before the note goes in", async () => {
    const publicConfirm = {
      ...CONFIRM,
      public: true,
      warnings: CONFIRM.warnings.filter((each) => !each.startsWith("Your GitHub login")),
      note_public_confirm: "This repository is public. Put my note in the pull request.",
    };
    const { section } = await page(
      source({ latest: { ...PROPOSED, draft: draft({ confirm: publicConfirm }) } }),
    );

    const text = readable(section as Element);
    expect(text).toContain("This repository is public. Put my note in the pull request.");
    expect(text).not.toContain("Your GitHub login is written into the pull request");
  });

  it("a refused check shows its sentence and the button again", async () => {
    const { section } = await page(
      source({
        latest: {
          ...PROPOSED,
          draft: draft({
            action: "Open draft pull request",
            check: {
              status: "refused",
              heading: "Not available",
              sentence: "GitHub says you do not have the access to acme/shop that this needs.",
              downloadable: false,
              expires_at: null,
            },
          }),
        },
      }),
    );

    expect(readable(section as Element)).toContain("GitHub says you do not have the access");
    expect(buttons(section).some((each) => each.textContent === "Open draft pull request")).toBe(
      true,
    );
  });

  it("FR26: an opened draft links to GitHub and says what holding CI means", async () => {
    const opened: SourceFix = {
      ...PROPOSED,
      state: "opened",
      heading: "Draft pull request",
      sentence: null,
      proposal: null,
      draft: draft({
        pull_request_state: "open",
        pull_request_url: "https://github.com/acme/shop/pull/7",
        pull_request_sentence: "A draft pull request is open: https://github.com/acme/shop/pull/7.",
        ci_note:
          "Nexovue's commit asked GitHub Actions to wait. Anything pushed since is not tracked here; check GitHub.",
      }),
    };
    const { section } = await page(source({ latest: opened }));

    const text = readable(section as Element);
    expect(text).toContain(
      "Nexovue's commit asked GitHub Actions to wait. Anything pushed since is not tracked here; check GitHub.",
    );
    const link = section?.querySelector<HTMLAnchorElement>(
      "a[href='https://github.com/acme/shop/pull/7']",
    );
    expect(link).not.toBeNull();
  });

  it("FR23: Delete this branch posts once its pull request is closed", async () => {
    const closed: SourceFix = {
      ...PROPOSED,
      state: "opened",
      heading: "Draft pull request",
      proposal: null,
      draft: draft({
        pull_request_state: "closed",
        pull_request_sentence:
          "This pull request was closed without merging: https://github.com/acme/shop/pull/7.",
        delete_branch_action: "Delete this branch",
      }),
    };
    const { api, section } = await page(source({ latest: closed }), {
      "POST /findings/f-1/fixes/s-1/delete-branch": json({ fix_id: "s-1" }),
    });

    click(buttons(section).find((each) => each.textContent === "Delete this branch"));
    for (let round = 0; round < 4; round += 1) await settle();

    expect(api.calls.some((call) => call.path === "/findings/f-1/fixes/s-1/delete-branch")).toBe(
      true,
    );
  });

  describe("Step 8: Run CI and following the pull request (T8.3; FR25, FR26, FR27, FR30)", () => {
    const HELD =
      "Nexovue's commit asked GitHub Actions to wait. Anything pushed since is not tracked here; check GitHub.";
    const CONFIRM_RUN =
      "This runs the model's code in this repository's CI, possibly with its secrets. Read the diff on GitHub first.";
    const opened = (overrides: Partial<NonNullable<SourceFix["draft"]>>): SourceFix => ({
      ...PROPOSED,
      state: "opened",
      heading: "Draft pull request",
      proposal: null,
      draft: draft({
        pull_request_state: "open",
        pull_request_url: "https://github.com/acme/shop/pull/7",
        pull_request_sentence: "A draft pull request is open: https://github.com/acme/shop/pull/7.",
        ...overrides,
      }),
    });

    it("CI held: Run CI opens FR25's confirmation first, and only its own click posts", async () => {
      const { api, section } = await page(
        source({
          latest: opened({
            ci_note: HELD,
            ci_action: "Run CI",
            ci_confirm: CONFIRM_RUN,
            ci_confirm_action: "Run CI now",
          }),
        }),
        { "POST /findings/f-1/fixes/s-1/run-ci": json({ fix_id: "s-1" }) },
      );

      expect(readable(section as Element)).toContain(HELD);
      click(buttons(section).find((each) => each.textContent === "Run CI"));
      await settle();
      expect(readable(section as Element)).toContain(CONFIRM_RUN);
      expect(api.calls.some((call) => call.path.endsWith("/run-ci"))).toBe(false);
      click(buttons(section).find((each) => each.textContent === "Run CI now"));
      for (let round = 0; round < 4; round += 1) await settle();

      expect(
        api.calls.filter((call) => call.path === "/findings/f-1/fixes/s-1/run-ci"),
      ).toHaveLength(1);
    });

    it("while write access is off the API sends no Run CI, and none is drawn", async () => {
      const { section } = await page(source({ latest: opened({ ci_note: HELD }) }));

      expect(buttons(section).some((each) => each.textContent === "Run CI")).toBe(false);
    });

    it("CI requested: the API's sentence replaces the action", async () => {
      const asked = "Asking GitHub to run CI: Nexovue is adding a commit with no skip marker.";
      const { section } = await page(
        source({ latest: opened({ ci_note: HELD, ci_status: asked }) }),
      );

      expect(readable(section as Element)).toContain(asked);
      expect(buttons(section).some((each) => each.textContent === "Run CI")).toBe(false);
    });

    it.each([
      ["merged", "This pull request was merged: https://github.com/acme/shop/pull/7."],
      [
        "closed",
        "This pull request was closed without merging: https://github.com/acme/shop/pull/7.",
      ],
      ["untracked", "Nexovue can no longer see this pull request; check GitHub."],
    ])("FR27: a %s pull request reads the API's sentence", async (state, sentence) => {
      const { section } = await page(
        source({ latest: opened({ pull_request_state: state, pull_request_sentence: sentence }) }),
      );

      expect(readable(section as Element)).toContain(sentence);
    });

    it("FR30: after a merge, the facts line states each time in the order it fell, with F28's deploy", async () => {
      const facts = {
        merged_at: "2026-11-21T10:00:00Z",
        deployed_at: "2026-11-21T10:20:00Z",
        finding_closed_at: "2026-11-21T09:30:00Z",
      };
      const { section } = await page(
        source({
          latest: opened({
            pull_request_state: "merged",
            pull_request_sentence:
              "This pull request was merged: https://github.com/acme/shop/pull/7.",
            merged_facts: facts,
          }),
        }),
      );

      const items = [...(section?.querySelectorAll("[data-testid='merged-facts'] li") ?? [])].map(
        (each) => each.textContent,
      );
      expect(items).toEqual([
        `This finding closed ${formatTimestamp(facts.finding_closed_at)}.`,
        `Merged ${formatTimestamp(facts.merged_at)}.`,
        `A deploy recorded with the merge's commit: ${formatTimestamp(facts.deployed_at)}.`,
      ]);
    });
  });
});

describe("the page follows a job it started (live test A11, 2026-11-28)", () => {
  const running = { status: "running" } as NonNullable<SourceFix["patch"]>;
  it("re-reads while the draft's own check runs, and while the draft is being opened", () => {
    expect(
      sourceFixWaiting({ ...GATHERED, state: "proposed", draft: { check: running } } as SourceFix),
    ).toBe(true);
    expect(sourceFixWaiting({ ...GATHERED, state: "approved", draft: null } as SourceFix)).toBe(
      true,
    );
    expect(sourceFixWaiting({ ...GATHERED, state: "opened", draft: null } as SourceFix)).toBe(
      false,
    );
    expect(sourceFixWaiting(null)).toBe(false);
  });
});

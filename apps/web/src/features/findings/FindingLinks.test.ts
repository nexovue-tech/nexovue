// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { CaseLink, FindingDetail } from "./api";
import {
  finding,
  json,
  minutesAgo,
  mountFinding,
  press,
  readable,
  settle,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The finding page's tracker issue (F22 T4.7; design, *State table*, *Creating an
 * issue*): the remaining state-table rows (AC133), resolve's confirmations naming the
 * issue (AC92), a hold (AC93), the status region hearing a link move (AC105), and Copy my
 * notes (AC127). Words and ages themselves are `link-words.test.ts`'s.
 */

afterEach(unmountAll);

const OWNER = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001";

function link(overrides: Partial<CaseLink> = {}): CaseLink {
  return {
    id: "l-1",
    kind: "github",
    role: "synced",
    sync_status: "in_sync",
    external_key: "acme/api#12",
    url: "https://github.com/acme/api/issues/12",
    place_key: "acme/api",
    seen_state: "open",
    wanted_state: null,
    comment_owed: false,
    tracker_closed_at: null,
    last_synced_at: minutesAgo(3),
    created_at: minutesAgo(60),
    last_failure: null,
    retry_helps: false,
    ...overrides,
  };
}

function activeCase(overrides: Partial<FindingDetail> = {}): FindingDetail {
  return finding({
    state: "closed",
    closed_reason: "recovered",
    closed_at: minutesAgo(30),
    case_state: "investigating",
    case_signal: "closed",
    case_opened_at: minutesAgo(60),
    case_taken_at: minutesAgo(60),
    assignee_id: OWNER,
    assignee_name: "owner",
    ...overrides,
  });
}

async function page(container: HTMLElement) {
  await waitFor(() => container.querySelector("h1"), "the finding's sentence");
  for (let round = 0; round < 5; round += 1) await settle();
}

describe("the case's issue on the page (AC133)", () => {
  it("shows the synced issue by its built address, and points at it to assign someone", async () => {
    stubApi(activeCase({ links: [link()] }), { role: "member" });
    const { container } = await mountFinding();
    await page(container);

    const issue = container.querySelector<HTMLAnchorElement>(
      'a[href="https://github.com/acme/api/issues/12"]',
    );
    expect(issue?.textContent).toBe("acme/api#12");
    expect(readable(container)).toContain("Open acme/api#12 to assign it");
    expect(readable(container)).toContain("An issue notifies nobody until someone is assigned.");
    // One synced issue per case: no second Create an issue.
    expect(readable(container)).not.toContain("Create an issue");
  });

  it("offers Try again only where it can help", async () => {
    stubApi(
      activeCase({
        links: [link({ sync_status: "waiting", last_failure: "timed_out", retry_helps: true })],
      }),
      { role: "member" },
    );
    const { container } = await mountFinding();
    await page(container);
    expect(readable(container)).toContain("Could not reach GitHub. Trying again.");
    expect(readable(container)).toContain("Try again");
  });

  it("says a hold plainly: closed in GitHub, signal still open (AC93)", async () => {
    stubApi(
      activeCase({
        state: "open",
        closed_at: null,
        closed_reason: null,
        case_signal: "open",
        links: [link({ seen_state: "closed", tracker_closed_at: minutesAgo(10) })],
      }),
      { role: "member" },
    );
    const { container } = await mountFinding();
    await page(container);
    const hold = container.querySelector('[data-testid="hold-line"]');
    expect(hold?.textContent).toMatch(
      /^Issue closed in GitHub at .+, and the signal is still open\.$/,
    );
  });

  it("tells a member with nothing connected to ask an admin, naming no other tracker", async () => {
    stubApi(activeCase(), {
      role: "member",
      extra: {
        "GET /connections": json({ available: true, unavailable_reason: null, connections: [] }),
      },
    });
    const { container } = await mountFinding();
    await page(container);
    await press(container, "Create an issue");
    await waitFor(
      () => readable(container).includes("Ask an admin to connect GitHub."),
      "the member's line",
    );
    expect(readable(container)).not.toContain("Jira");
  });

  it("previews exactly what will be written, then creates it", async () => {
    const api = stubApi(activeCase(), {
      role: "member",
      extra: {
        "GET /connections": json({
          available: true,
          unavailable_reason: null,
          connections: [
            {
              id: "c-1",
              kind: "github",
              state: "working",
              repositories: [{ id: "p-1", key: "acme/api", is_private: true, is_reachable: true }],
            },
          ],
        }),
        "GET /findings/f-1/links/preview": json({
          place_id: "p-1",
          place_key: "acme/api",
          title: "nexovue: slower than normal on checkout",
          body: "nexovue detected this on `checkout`.\n",
          public: false,
          refusal: null,
        }),
        "POST /findings/f-1/links": json({
          finding: activeCase({
            links: [link({ sync_status: "creating", external_key: null, url: null })],
          }),
          link_id: "l-1",
        }),
      },
    });
    const { container } = await mountFinding();
    await page(container);
    await press(container, "Create an issue");
    const select = await waitFor(
      () => container.querySelector<HTMLSelectElement>("select"),
      "the repository picker",
    );
    await act(async () => {
      select.value = "p-1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await waitFor(() => container.querySelector('[data-testid="issue-preview"]'), "the preview");
    expect(readable(container)).toContain("nexovue: slower than normal on checkout");
    await press(container, "Create issue in acme/api");
    expect(api.to("POST", "/findings/f-1/links")[0]?.body).toEqual({ place_id: "p-1" });
  });
});

describe("resolving with the issue (AC92)", () => {
  it("confirms, naming the issue and the comment, and can leave it open", async () => {
    const api = stubApi(activeCase({ links: [link()] }), {
      role: "member",
      extra: {
        "PATCH /findings/f-1/case": json({
          finding: activeCase({ case_state: "resolved", links: [link()] }),
          email_outcome: "none",
        }),
      },
    });
    const { container } = await mountFinding();
    await page(container);
    await press(container, "Resolve");

    expect(readable(container)).toContain("This closes acme/api#12 and adds one comment there.");
    expect(api.to("PATCH", "/findings/f-1/case")).toEqual([]);
    await press(container, "Resolve here, leave the issue open");
    expect(api.to("PATCH", "/findings/f-1/case")[0]?.body).toEqual({
      case_state: "resolved",
      leave_issue_open: true,
    });
    await waitFor(
      () => readable(container).includes("Resolved here. acme/api#12 stays open."),
      "the done line",
    );
  });

  it("on an already closed issue offers one Resolve and says nothing more goes there", async () => {
    const held = link({ seen_state: "closed", tracker_closed_at: minutesAgo(10) });
    stubApi(activeCase({ links: [held] }), {
      role: "member",
      extra: {
        "PATCH /findings/f-1/case": json({
          finding: activeCase({ case_state: "resolved", links: [held] }),
          email_outcome: "none",
        }),
      },
    });
    const { container } = await mountFinding();
    await page(container);
    await press(container, "Resolve");

    expect(readable(container)).toContain(
      "acme/api#12 is already closed in GitHub. Nothing more goes there.",
    );
    expect(readable(container)).not.toContain("leave the issue open");
    const confirm = [...container.querySelectorAll("fieldset button")].find(
      (button) => button.textContent === "Resolve",
    );
    await act(async () => (confirm as HTMLButtonElement | undefined)?.click());
    await waitFor(
      () =>
        readable(container).includes(
          "Resolved. acme/api#12 was already closed, so nothing changes there.",
        ),
      "the done line",
    );
  });
});

describe("the status region and the person's notes", () => {
  it("offers Copy my notes, built from the person's own notes alone (AC127)", async () => {
    const api = stubApi(activeCase({ links: [link()] }), {
      role: "owner",
      extra: {
        "GET /findings/f-1/timeline": json({
          events: [
            {
              id: "n1",
              kind: "note_added",
              at: minutesAgo(10),
              actor_kind: "person",
              actor_id: OWNER,
              actor_name: "owner",
              detail: { note_id: "x" },
              note_body: "rolled back",
            },
            {
              id: "n2",
              kind: "note_added",
              at: minutesAgo(8),
              actor_kind: "person",
              actor_id: "someone-else",
              actor_name: "Ravi",
              detail: { note_id: "y" },
              note_body: "my token is hunter2",
            },
          ],
          next_before: null,
        }),
      },
    });
    const { container } = await mountFinding();
    await page(container);
    const copied: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (text: string) => copied.push(text) },
    });
    const copy = await waitFor(
      () => container.querySelector<HTMLButtonElement>('button[aria-label="Copy my notes"]'),
      "the copy button",
    );
    const before = api.calls.length;
    await act(async () => copy.click());
    await settle();
    expect(copied).toEqual(["rolled back"]);
    // Nothing is sent: the notes reach the clipboard and no request carries them.
    expect(api.calls.slice(before).filter((call) => call.method !== "GET")).toEqual([]);
  });
});

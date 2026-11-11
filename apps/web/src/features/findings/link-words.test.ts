import { describe, expect, it } from "vitest";

import type { CaseLink, TimelineEvent } from "./api";
import {
  canPromote,
  falseAlarmIssueSentence,
  holdLine,
  issueEventSentence,
  linkAnnouncement,
  linkSentence,
  linkStatusWords,
  myNotesText,
  preselectedPlace,
  promoteWarning,
  publicPlaceLine,
  resolveChoice,
  resolveDoneSentence,
  syncedLink,
} from "./link-words";

/**
 * What a case's tracker issue says on the finding page (F22 T4.7; spec, *Link and
 * connection states*, *Words after an action*). Pure, on a fixed clock, so each status,
 * each reader and each age is one row here (AC116, AC119), and the resolve and false
 * alarm choices name the issue they close (AC58, AC92).
 */

const NOW = Date.parse("2026-11-09T12:00:00Z");
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

function link(overrides: Partial<CaseLink> = {}): CaseLink {
  return {
    id: "link-1",
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

const active = { caseActive: true, admin: false, now: NOW };

describe("a link's words change with age (AC119)", () => {
  it("says it is creating, then after 2 minutes that sync cannot be reached", () => {
    const creating = link({ sync_status: "creating", external_key: null, url: null });
    expect(linkSentence({ ...creating, created_at: minutesAgo(1) }, active)).toBe(
      "Creating the issue. This may take a moment",
    );
    expect(linkSentence({ ...creating, created_at: minutesAgo(2) }, active)).toBe(
      "Not created yet. nexovue cannot reach its sync service. The case still works.",
    );
  });

  it("says when it was synced, then past 30 minutes on an active case how long it has not been", () => {
    expect(linkSentence(link(), active)).toBe("acme/api#12 · synced 3 min. ago");
    expect(linkSentence(link({ last_synced_at: minutesAgo(29) }), active)).toBe(
      "acme/api#12 · synced 29 min. ago",
    );
    expect(linkSentence(link({ last_synced_at: minutesAgo(120) }), active)).toBe(
      "acme/api#12 · Not synced for 2 hours",
    );
    // A resolved case is read hourly: an hour is not late.
    expect(
      linkSentence(link({ last_synced_at: minutesAgo(50) }), { ...active, caseActive: false }),
    ).toBe("acme/api#12 · synced 50 min. ago");
  });
});

describe("each status, by reader (AC116)", () => {
  it.each([
    [
      { sync_status: "waiting", last_failure: "server_error" },
      "acme/api#12 · Could not reach GitHub. Trying again. Last synced 3 min. ago",
      "acme/api#12 · Could not reach GitHub. Trying again. Last synced 3 min. ago",
    ],
    [
      { sync_status: "needs_attention", last_failure: "repository_unreachable" },
      "acme/api#12 · nexovue can no longer read acme/api. Check the App's repositories in GitHub, then refresh the connection in settings.",
      "acme/api#12 · Ask an admin to reconnect GitHub. The case still works.",
    ],
    [
      { sync_status: "needs_attention", last_failure: "moved_out_of_reach" },
      "acme/api#12 · This issue moved to a repository nexovue does not reach. Add that repository to the App in GitHub.",
      "acme/api#12 · Ask an admin to reconnect GitHub. The case still works.",
    ],
    [
      { sync_status: "needs_attention", last_failure: "reopen_blocked" },
      "acme/api#12 · Reopened in GitHub, but another case is active on this service, so this one stays resolved.",
      "acme/api#12 · Reopened in GitHub, but another case is active on this service, so this one stays resolved.",
    ],
    [
      { sync_status: "refused", last_failure: "public_repository", external_key: null },
      "Not created: the repository is public. An admin can allow it in settings.",
      "Not created: the repository is public. Ask an admin.",
    ],
    [
      { sync_status: "gone" },
      "acme/api#12 · Cannot find this issue. It may be deleted or moved",
      "acme/api#12 · Cannot find this issue. It may be deleted or moved",
    ],
    [
      { sync_status: "stopped" },
      "acme/api#12 · GitHub was disconnected. This issue is no longer followed.",
      "acme/api#12 · GitHub was disconnected. This issue is no longer followed.",
    ],
  ] as const)("%o", (overrides, forAdmin, forMember) => {
    const shown = link(overrides as Partial<CaseLink>);
    expect(linkSentence(shown, { ...active, admin: true })).toBe(forAdmin);
    expect(linkSentence(shown, active)).toBe(forMember);
  });

  it("names the date a link stopped being followed", () => {
    const resolvedLong = link({
      sync_status: "not_followed",
      last_synced_at: minutesAgo(2 * 24 * 60),
    });
    expect(linkSentence(resolvedLong, { ...active, caseActive: false, zone: "UTC" })).toBe(
      "acme/api#12 · No longer followed since 7 Nov, 12:00 UTC. This issue may have changed. Reopen the case to follow it again",
    );
  });
});

describe("resolving with a synced issue always confirms, naming it (AC92)", () => {
  it("is one tap with no link and the signal closed", () => {
    expect(resolveChoice({ links: [], case_signal: "closed" })).toEqual({ kind: "one_tap" });
  });

  it("names the issue and the comment, and offers to leave it open", () => {
    expect(resolveChoice({ links: [link()], case_signal: "closed" })).toEqual({
      kind: "synced",
      issueKey: "acme/api#12",
      sentences: ["This closes acme/api#12 and adds one comment there."],
    });
  });

  it("says an already closed issue gets nothing more, and offers one button", () => {
    const held = link({ seen_state: "closed", tracker_closed_at: minutesAgo(10) });
    expect(resolveChoice({ links: [held], case_signal: "open" })).toEqual({
      kind: "held",
      issueKey: "acme/api#12",
      sentences: [
        "The signal is still open: the problem is still being measured.",
        "acme/api#12 is already closed in GitHub. Nothing more goes there.",
      ],
    });
  });

  it("ignores a reference or a stopped link", () => {
    for (const other of [link({ role: "reference" }), link({ sync_status: "stopped" })]) {
      expect(resolveChoice({ links: [other], case_signal: "closed" }).kind).toBe("one_tap");
    }
  });

  it("says what happens after each choice", () => {
    expect(resolveDoneSentence("acme/api#12", "will_close")).toBe(
      "Resolved. acme/api#12 will be closed when sync next runs. The link below shows when it has.",
    );
    expect(resolveDoneSentence("acme/api#12", "left_open")).toBe(
      "Resolved here. acme/api#12 stays open.",
    );
    expect(resolveDoneSentence("acme/api#12", "already_closed")).toBe(
      "Resolved. acme/api#12 was already closed, so nothing changes there.",
    );
  });
});

describe("a hold (AC93) and a false alarm (AC58)", () => {
  it("says the issue closed while the signal is open", () => {
    const held = link({ seen_state: "closed", tracker_closed_at: "2026-11-09T10:10:00Z" });
    expect(holdLine({ links: [held] }, "UTC", NOW)).toBe(
      "Issue closed in GitHub at 10:10 UTC, and the signal is still open.",
    );
    expect(holdLine({ links: [link()] }, "UTC", NOW)).toBeNull();
  });

  it("names the issue a false alarm closes, or that it already is", () => {
    expect(falseAlarmIssueSentence({ links: [link()] })).toBe(
      "This also closes acme/api#12 and adds one comment there.",
    );
    expect(falseAlarmIssueSentence({ links: [link({ seen_state: "closed" })] })).toBe(
      "acme/api#12 is already closed in GitHub. Nothing more goes there.",
    );
    expect(falseAlarmIssueSentence({ links: [] })).toBeNull();
  });
});

describe("the status region hears a link's status change, never its age (AC105)", () => {
  it("announces a status move and nothing for a fresh sync time", () => {
    expect(linkAnnouncement([link()], [link({ last_synced_at: minutesAgo(1) })])).toBeNull();
    expect(
      linkAnnouncement([link()], [link({ sync_status: "waiting", last_failure: "timed_out" })]),
    ).toBe("acme/api#12 is now waiting.");
    expect(
      linkAnnouncement(
        [link({ sync_status: "creating", external_key: null })],
        [link({ sync_status: "in_sync" })],
      ),
    ).toBe("acme/api#12 is now in sync.");
    expect(linkAnnouncement([], [link({ sync_status: "creating", external_key: null })])).toBe(
      "An issue is now creating.",
    );
  });
});

describe("Copy my notes copies only the person's own notes (AC127)", () => {
  const note = (author: string, body: string, at: string): TimelineEvent => ({
    id: `${author}-${at}`,
    kind: "note_added",
    at,
    actor_kind: "person",
    actor_id: author,
    actor_name: author,
    note_body: body,
  });

  it("keeps the person's notes, oldest first, and nothing else", () => {
    const events: TimelineEvent[] = [
      note("asha", "restarted the pods", "2026-11-09T11:00:00Z"),
      note("ravi", "my secret token is x", "2026-11-09T10:30:00Z"),
      {
        id: "e",
        kind: "case_started",
        at: "2026-11-09T10:00:00Z",
        actor_kind: "person",
        actor_id: "asha",
      },
      note("asha", "rolled back 1.4.2", "2026-11-09T10:15:00Z"),
    ];
    expect(myNotesText(events, "asha")).toBe("rolled back 1.4.2\n\nrestarted the pods");
    expect(myNotesText(events, "nobody")).toBe("");
  });
});

describe("timeline rows about the issue (FR16)", () => {
  it.each([
    [{ kind: "link_added", actor_kind: "person", actor_name: "Asha" }, "Asha asked for an issue"],
    [{ kind: "issue_created", actor_kind: "system" }, "The issue was created"],
    [
      { kind: "issue_closed", actor_kind: "tracker", detail: { close_reason: "not_planned" } },
      "The issue was closed in the tracker, as not planned",
    ],
    [
      { kind: "issue_closed", actor_kind: "system", detail: { close_reason: "completed" } },
      "nexovue closed the issue, as completed",
    ],
    [{ kind: "issue_reopened", actor_kind: "tracker" }, "The issue was reopened in the tracker"],
    [{ kind: "issue_gone", actor_kind: "tracker" }, "The issue cannot be found"],
    [{ kind: "issue_moved", actor_kind: "tracker" }, "The issue moved"],
    [
      { kind: "link_removed", actor_kind: "person", actor_name: "Ravi" },
      "Ravi removed the issue here",
    ],
    [
      {
        kind: "tracker_write_failed",
        actor_kind: "system",
        detail: { failure: "public_repository" },
      },
      "nexovue did not write to the issue: the repository is public",
    ],
  ] as const)("%o", (event, words) => {
    expect(
      issueEventSentence({ id: "x", at: "2026-11-09T10:00:00Z", ...event } as TimelineEvent),
    ).toBe(words);
  });

  it("finds the case's live synced link", () => {
    expect(syncedLink({ links: [link({ role: "reference" }), link({ id: "s" })] })?.id).toBe("s");
    expect(syncedLink({ links: [link({ sync_status: "stopped" })] })).toBeNull();
  });
});

describe("an attached issue, syncing one, and a public repository (F22 T6.3)", () => {
  const reader = { caseActive: true, admin: false, now: NOW };
  const reference = (overrides: Partial<CaseLink> = {}) =>
    link({ role: "reference", external_key: "acme/api#7", ...overrides });

  it.each([
    [
      { seen_state: "open" as const },
      "Open in GitHub, read 3 min. ago. It does not change this case",
    ],
    [
      { seen_state: "closed" as const, last_synced_at: minutesAgo(200) },
      "Closed in GitHub, read 3 hr. ago. It does not change this case",
    ],
    [{ seen_state: null, last_synced_at: null }, "Not read yet"],
  ])("a reference reads its own status, never late or synced (AC53): %o", (overrides, words) => {
    expect(linkStatusWords(reference(overrides), reader)).toBe(words);
  });

  it.each([
    [false, null, "Not created: the repository is public. Ask an admin."],
    [true, null, "Not created: the repository is public. An admin can allow it in settings."],
    [false, "acme/api#7", "Not synced: the repository is public. Ask an admin."],
    [
      true,
      "acme/api#7",
      "Not synced: the repository is public. An admin can allow it in settings.",
    ],
  ])("a refusal for a public repository, admin %s, issue %s", (admin, key, words) => {
    const refused = link({
      sync_status: "refused",
      last_failure: "public_repository",
      external_key: key,
    });
    expect(linkStatusWords(refused, { ...reader, admin })).toBe(words);
  });

  it("says a public repository's link is readable by anyone, and nothing otherwise", () => {
    expect(publicPlaceLine(link({ place_public: true }))).toBe(
      "acme/api is public: anyone can read this issue.",
    );
    expect(publicPlaceLine(link({ place_public: false }))).toBeNull();
    expect(publicPlaceLine(link())).toBeNull();
  });

  it("warns, before syncing, that whoever can close the issue can resolve the case", () => {
    expect(promoteWarning(reference())).toBe(
      "Sync acme/api#7 with this case? Anyone who can close it in GitHub can then resolve this case, and when the case ends here, nexovue closes it there with one comment.",
    );
  });

  it.each([
    ["a read reference on an active case", [reference()], "investigating", true],
    ["one not read yet", [reference({ last_synced_at: null })], "investigating", false],
    ["one gone", [reference({ sync_status: "gone" })], "investigating", false],
    ["a resolved case", [reference()], "resolved", false],
    ["a case with a synced issue", [reference(), link({ id: "s" })], "investigating", false],
  ])("offers to sync %s: %s", (_, links, caseState, offered) => {
    expect(canPromote(links[0] as CaseLink, { links, case_state: caseState })).toBe(offered);
  });

  it("preselects the service's mapped repository only while it is reachable", () => {
    const places = [{ id: "p-1" }, { id: "p-2" }];
    const mappings = [
      { service_name: "checkout", place_id: "p-2" },
      { service_name: "payments", place_id: "p-9" },
    ];
    expect(preselectedPlace(places, mappings, "checkout")).toBe("p-2");
    expect(preselectedPlace(places, mappings, "payments")).toBeNull();
    expect(preselectedPlace(places, mappings, "search")).toBeNull();
    expect(preselectedPlace(places, mappings, null)).toBeNull();
  });
});

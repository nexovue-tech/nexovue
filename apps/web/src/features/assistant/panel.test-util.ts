import { vi } from "vitest";

import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { PROJECT_ID } from "@/shared/router-harness.test-util";

import type { Availability, MessageView, StepView } from "./api";

/**
 * Chat's panel mounted in a router with only the network replaced (F4 T2.11's tests). Each
 * test says what the API answers; every call is recorded.
 */

export const CONVERSATION_ID = "7a1f0a52-7d64-4c87-9b7a-0f0d1d1d1d02";
export const MESSAGE_ID = "8b1f0a52-7d64-4c87-9b7a-0f0d1d1d1d03";
const API_PREFIX = "/api/v1";

export const READY: Availability = {
  state: "ready",
  provider_label: "a model on your own server",
  tested: false,
  questions_left_today: 47,
  resets_at: "2026-11-13T00:00:00Z",
  waiting_for: [],
  may_manage: false,
};

export function step(overrides: Partial<StepView> = {}): StepView {
  return {
    key: "s1",
    position: 1,
    tool: "list_services",
    words: "Reading services",
    display_arguments: null,
    search_hidden: false,
    from_time: "2026-11-12T09:00:00Z",
    to_time: "2026-11-12T09:30:00Z",
    row_count: 2,
    was_cut: false,
    state: "read",
    failure: null,
    link: {
      path: "/services",
      query: { project: PROJECT_ID },
      caveat: "The range is not applied here.",
    },
    ...overrides,
  };
}

export function message(overrides: Partial<MessageView> = {}): MessageView {
  return {
    id: MESSAGE_ID,
    conversation_id: CONVERSATION_ID,
    project_id: PROJECT_ID,
    question: "What is failing right now?",
    context: {},
    state: "answered",
    phase: null,
    failure_reason: null,
    rejection_reason: null,
    abstain_reason: null,
    answer: {
      sentences: [
        { text: "checkout had 12 errors.", label: "counted", cause_warning: false, cites: ["s1"] },
      ],
      removed_sentence_count: 0,
      not_verified: [],
    },
    wrong_reason: null,
    created_at: "2026-11-12T09:30:00Z",
    finished_at: "2026-11-12T09:30:20Z",
    steps: [step()],
    ...overrides,
  };
}

export interface Answers {
  role?: TestRole;
  availability?: Availability;
  /** The question the conversation list says is in flight, as after a reload. */
  inFlight?: boolean;
  /** Successive answers to the message poll; the last repeats. A number is a status. */
  polls?: (MessageView | number)[];
  ask?: MessageView | { status: number; code: string; detail: string };
}

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

// Fake responses only: what a code means is `shared/api-client.ts`'s alone.
const OK = 200;
const ACCEPTED = 202;
const NOT_FOUND = 404;
const FIRST_REFUSAL = 400;

function reply(body: unknown, code = OK): Response {
  return new Response(JSON.stringify(body), {
    status: code,
    headers: {
      "content-type": code < FIRST_REFUSAL ? "application/json" : "application/problem+json",
    },
  });
}

function problem(code: number, extra: Record<string, unknown> = {}): Response {
  return reply({ type: "about:blank", title: "Refused", status: code, ...extra }, code);
}

export function stubApi(answers: Answers = {}): Call[] {
  const calls: Call[] = [];
  const polls = [...(answers.polls ?? [message()])];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (path === "/auth/me") return reply(identityFor(answers.role ?? "member"));
      if (path === "/projects") {
        return reply({
          projects: [
            {
              id: PROJECT_ID,
              slug: "production",
              name: "production",
              first_reported_on: "2026-11-01",
            },
          ],
        });
      }
      if (path === "/assistant/availability") return reply(answers.availability ?? READY);
      if (path === "/assistant/conversations" && method === "GET") {
        return reply(
          answers.inFlight
            ? [
                {
                  id: CONVERSATION_ID,
                  project_id: PROJECT_ID,
                  title: "q",
                  last_message_at: "2026-11-12T09:30:00Z",
                  in_flight_message_id: MESSAGE_ID,
                },
              ]
            : [],
        );
      }
      if (path === `/assistant/conversations/${CONVERSATION_ID}`) {
        return reply({
          id: CONVERSATION_ID,
          project_id: PROJECT_ID,
          title: "q",
          messages: [],
          has_older: false,
        });
      }
      if (path === `/assistant/conversations/${CONVERSATION_ID}/messages/${MESSAGE_ID}`) {
        const next = polls.length > 1 ? polls.shift() : polls[0];
        return typeof next === "number" ? problem(next) : reply(next);
      }
      if (path.endsWith("/stop")) return reply(message({ state: "stopped" }));
      if (path === "/assistant/conversations" && method === "POST") {
        const asked = answers.ask ?? message({ state: "queued", answer: null, steps: [] });
        return "status" in asked
          ? problem(asked.status, { code: asked.code, detail: asked.detail })
          : reply(asked, ACCEPTED);
      }
      return problem(NOT_FOUND);
    }),
  );
  return calls;
}

export { mount, PROJECT_ID, settle, unmountAll } from "@/shared/router-harness.test-util";

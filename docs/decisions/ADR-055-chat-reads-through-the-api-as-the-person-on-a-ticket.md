# ADR-055: Chat's model loop reads through the API, as the person, on a ticket

## Status

Accepted 2026-11-02 by the founder with the F4 design ([`design.md`](../features/ai-chatbot/design.md),
*Proposed ADR*, the first; FD24 set the direction). Filed 2026-11-11 with F4 plan Step 2, which built
it. Numbered at filing; renumbered at commit if another feature files first.

## Context

Chat must read telemetry and control-plane data to answer. The AI process holds no telemetry account
(FD2; [ADR-040](ADR-040-ai-runs-in-its-own-role-with-its-own-account-and-network.md)), and the
scheduler cannot import the API. Reads must keep the person's access rules, rate limits and tenant
checks. The code shows `CurrentReader` is the one door that already does all three, and it knows a
person and an API token. Provider SDKs and agent frameworks are out
([ADR-047](ADR-047-one-provider-interface-three-wire-formats-no-sdk.md)): they bring their own HTTP
clients, which lose the connect-time address guard.

## Decision

1. **The loop runs in `scheduler-insight`**, queue `assistant`, through F3's three job functions,
   which gained the queue and a queue list (migration 0073). Each tool is a GET on `/api/v1`,
   reached over the `ai` network, which the API joins, by a small pycurl client with a fixed base
   URL, no redirects, and every value encoded whole. Provider calls use F3's wire formats, which gained
   tool calling as a separate chat request type, and F3's guarded transport.
2. **A third read credential, `nvat_`,** minted by the API in the same transaction as the question, for
   one question, one person and one project; its SHA-256 on the message, its value sealed under
   `AI_SECRET_KEY` for that message alone. It is good only for an HTTP GET whose matched handler is in
   `TICKET_HANDLERS`, only while its message is in flight and inside its deadline, and is rechecked on
   **every** read against the person (removed, signed out everywhere, role), the scope (deleted
   project or organization) and chat's standing (AI and chat on, project not opted out, consent
   current).
3. **A ticket reads as neither a person nor a token:** `Reader.is_person` is false, so it gets no
   person fields; `project_scope` is the question's. It pays from the person's and organization's own
   rate-limit allowance on the path's tier.
4. **`TICKET_HANDLERS` is built from one catalog** (`nexovue_platform.assistant_tools`), held by a test
   to the read surface and to each route's response model. A field not in the catalog is never sent.
5. **The ticket check is registered on the application,** not imported by `auth/dependencies.py`, so
   the dependency every route reads through reaches no AI code.
6. **Row-level security on questions and steps** shows the AI account only questions in flight or
   still holding tokens; every final write is `assistant_finish_message`, `SECURITY DEFINER`, because
   a policy cannot let an account write a row it may then not see. Proved as the account on a
   superuser-owned and a non-superuser-owned database (status, *Validation*).

## Consequences

- A steered model can read at most what its one person could read, in one project, through a fixed
  list, with no write.
- A compromised AI process can use only live tickets, sees only questions in flight, cannot rewrite a
  finished answer, and holds no telemetry account. It sits on the `ai` network beside the API's whole
  port, which is accepted.
- Each read costs an internal HTTP hop (unmeasured) and a recheck of the person and standing.
- The read surface becomes load-bearing for chat: adding a route to it needs a second decision on
  whether chat gets it.
- The ticket's prefix and the read surface's additions are hard to change after a first customer.

## Alternatives rejected

- **Chat in the API.** Puts model-driven code beside every secret.
- **Read services moved into `nexovue_platform`.** A huge move that loses the rate limiter and access
  checks.
- **A reader account in the AI role.** A second read path with no person's checks.
- **A ticket with the person's full reach, or one minted by the AI role.** Wider than one question.
- **Allowing by path text.** A nested router's path names no one route (`/search` for
  `/api/v1/logs/search`); the matched handler does.
- **An SDK or agent framework for tool calling.** Loses the address guard (ADR-047).

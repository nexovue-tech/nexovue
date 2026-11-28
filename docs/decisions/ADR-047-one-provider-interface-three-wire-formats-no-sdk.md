# ADR-047: One provider interface, three wire formats, no SDK

## Status

Accepted 2026-11-02 by the founder with the F3 design ([`design.md`](../features/ai-insights/design.md),
*Proposed ADRs*, the fourth) and the foundation's FD3; filed 2026-11-11 with F3 plan Step 4, which built
it. Revives what decision 0014 aimed at, on today's facts. **No format has made a real call yet**
(below), so none is offered on the settings screen.

## Context

A customer's AI is either their own account with a provider or a model on their own network
(foundation, section 2). One OpenAI-style format cannot honestly carry both: Anthropic calls its
OpenAI-compatible layer not production-ready, and Bedrock's India route needs Converse. Provider SDKs
and LLM frameworks are on the no-AI deny-list, and an SDK opens its own sockets, which would bypass
the prober's connect-time address guard, the one place an address cannot change after it is judged.

## Decision

1. **Three plain-HTTP wire formats** in `nexovue_platform.ai.formats`: `openai_chat`,
   `anthropic_messages`, `bedrock_converse`. Each is two pure functions: `build` turns an `AIRequest`
   into the exact bytes (one canonical JSON form, so a rebuild hashes the same), and `parse` turns a
   status and body into an `AIResponse` with one closed outcome. No field for temperature, tool
   choice or top-p.
2. **All HTTP through the guarded client**, in `nexovue_scheduler.insight.transport`, over
   `probes.http_check.perform`: redirects never followed; plain `http` only to an address the
   operator listed in `AI_PRIVATE_ENDPOINTS`; loopback and link-local refused even when listed; a
   header with a control character never sent; a 1 MiB answer cap. The API imports the pure parts
   only, to show the preview and rebuild what a run sent; it never calls a provider.
3. **Both customer options are one row** of `ai_connections`, differing in values. No code path asks
   "is this local?" except the screen's sentence about where data goes.
4. **A format is offered on the screen only after a dated real call** recorded in
   `nexovue_platform.ai.real_calls` (AC77). nexovue validates every answer itself, whatever a provider
   promises (plan Step 5).

## Alternatives rejected

- **One OpenAI-style format.** Rests on a layer Anthropic does not stand behind, and misses Converse.
- **Provider SDKs, or a framework.** Each brings its own HTTP client, which loses the address guard,
  and they are on the deny-list the no-AI suites hold.

## Consequences

- Three formats to keep working; a provider's change breaks one. New provider features are not
  automatic.
- **The address guard's promise widens**: a private address was reachable only in development; now an
  operator's named list of addresses and ports opens one for an AI connection. Loopback, link-local,
  reserved and multicast stay refused, and a range wider than /16 (IPv4) or /48 (IPv6) is refused.
- Field names were read from each provider's documentation on 2026-11-11; every parse fixture is
  marked "written from documentation" until a real response is recorded.

## Evidence

- `test_ai_wire_formats.py`: each format's shapes for success, a 200 refusal, length, a bad key, an
  unknown model, rate limits, server errors and unreadable bodies; no sampling control in any request;
  the model percent-encoded in Bedrock's path.
- `test_insight_check_live.py`: against a recording site, as the AI account: the constant body, no
  redirect followed, an unlisted private address not contacted, a key sealed for another address not
  opened.
- **Owed:** one dated real call per format (`tools/ai-eval/real_call.py`), which needs the founder's
  free-tier key (foundation item 4); and Bedrock's AWS-signed path, which is plan T4.9's spike and is
  not built (a Bedrock API key as a bearer token is).

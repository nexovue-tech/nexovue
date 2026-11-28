# Change: ai-egress-block — the AI container reaches the outside only through a proxy that refuses internal addresses

## Classification

Security, infrastructure. F5 readiness blocker B1, F3 readiness S1, F5 T4.11 and open decision 43.
The founder required the block before the first deploy (2026-11-27, foundation item 37a), and on
2026-11-28 chose to build it before `v0.2.0-rc.1` rather than ship the firewall note (option b).

## Current behavior and problem

`scheduler-insight` holds every organization's sealed provider keys and the AI account. Its only
network, `ai`, is not `internal` (`deploy/compose/compose.infra.yaml:198-203`), so the container
has a direct route to anything the host can reach, including the cloud metadata address
`169.254.169.254`, which hands out the server's own cloud credentials.

The application already refuses link-local and loopback addresses at connect time
(`probes/addresses.py`, `ConnectGuard`; `insight/transport.py` docstring). That guard runs
inside the process it protects: code that has broken into the process does not call it. The
fence test only prints the metadata answer (`test_insight_network_live.py:121`), because
"Compose is not an outbound firewall".

## Intended behavior

- `ai` becomes an `internal` network: nothing on it has a route out.
- A new service, `ai-egress`, sits on `ai` and on its own outbound network, `ai-out`, which no
  other service joins. It is an allow-by-policy forward proxy that resolves each name itself and
  refuses any address that is not publicly routable, with link-local and every known metadata
  address refused always, even when listed.
- The operator's `AI_PRIVATE_ENDPOINTS` (a local Ollama or vLLM) are the only private addresses
  it lets through, by address and port, as today.
- `scheduler-insight` sends every provider call through `ai-egress`. Postgres and the API stay
  reached directly over `ai`.
- Optional, off by default: `AI_EGRESS_ALLOWED_HOSTS` narrows the way out to named provider hosts
  (spec D49, AC53).
- Proved from inside the running container: the metadata address, a store, and an unlisted
  private address fail by every route; a public host and a listed private endpoint work.

## Affected specifications and architecture

F3 spec AC42 (the metadata address must now be refused, not only recorded) and its *What may be
said* row ("outbound limits are your host firewall's job"); F5 spec AC53 and D49; ADR-040 (the
AI role's network); ADR-019 (images); F3 design *Network*; F5 design A13.

## Risks and rollback

- A provider call that worked before can fail through the proxy (an IPv6-only host, a provider
  behind an unusual address). Mitigation: the fence test runs a real public call; the failure
  reads as `address_refused` with the proxy's reason in the logs.
- One more container to run and patch. It is small, holds no secret, and has no volume.
- Rollback: a release that restores the previous compose files. Nothing in the database changes.

## Plan and validation

`plan.md`. Validation: `just check`, then `just insight-fence` on the merged install layout.

## Status

`status.md`.

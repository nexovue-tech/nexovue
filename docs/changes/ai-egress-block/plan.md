# Plan: ai-egress-block

> **Status: waiting for the founder's approval.** No file outside this folder is edited before it.
> Record: `change.md`. Written 2026-11-28 on `feature/phase-2`.

## Preconditions

- The founder's choice of proxy image (Question 1) and of the default for the host allow-list
  (Question 2).
- The 13 uncommitted files of the F19 fix and the founder's decisions are committed first, so
  this change is its own commit.

## Current system

Read from the code:

- **Network.** `deploy/compose/compose.infra.yaml:198-203`: `ai` is a plain network, "Not
  `internal`: the role calls providers outside." Postgres joins `[default, ai]` (`:146`).
  `scheduler-insight` joins `[ai]` only (`nexovue.yaml:506`); the API joins `[default, ai]`
  (`nexovue.yaml:296`), and so does Prometheus when monitoring is on (`nexovue.yaml:535`).
  The development stack mirrors this (`docker-compose.yaml:189, 677, 705`); the overlays
  `nexovue.hosted.yaml:54`, `nexovue.quickstart.yaml:49` and `nexovue.build.yaml:34` name
  `scheduler-insight` too.
- **The provider call.** `insight/transport.py:63` `send` calls `probes/http_check.py:109`
  `perform`, a pycurl client. Its `ConnectGuard` is installed as `OPENSOCKETFUNCTION`
  (`http_check.py:189`), so the address is judged at connect time; `PrivateEndpoints`
  (`addresses.py:145`) parses `AI_PRIVATE_ENDPOINTS`, and loopback and link-local stay refused
  even when listed. No proxy option exists anywhere in `perform`.
- **The fence.** `just insight-fence` (`tools/insight-fence/fence.sh`) installs a kit under its
  own Compose project and runs `apps/scheduler/tests/test_insight_network_live.py`. Its metadata
  test only prints the answer (`:121-127`); its peer test expects a site on the Docker host to be
  reachable at the `ai` gateway (`:113`), which an internal network will (rightly) close.
- **Images.** Infrastructure images are pinned by tag in `compose.infra.yaml` (Kafka, ClickHouse,
  pgvector, SeaweedFS), outside ADR-019's two nexovue images.

## Change required

A proxy between `scheduler-insight` and the outside, with `ai` made internal, and the provider
call taught to use it. Nothing in the database changes.

## Impact

| Area | Change |
|---|---|
| Compose | `ai` internal; new network `ai-out`; new service `ai-egress` in every layout that runs `scheduler-insight` |
| Scheduler | `perform` gains a proxy; `transport.send` uses it when `AI_EGRESS_PROXY` is set |
| Settings | `AI_EGRESS_PROXY` (set by compose), `AI_EGRESS_ALLOWED_HOSTS` (optional) |
| Tests | fence test asserts the block; unit tests for the proxy path; a compose structure test |
| Docs | customer setup pages, release notes, first deploy step 5, F3/F5 specs and readiness, an ADR |

## Reuse

- `PrivateEndpoints.parse` already validates `AI_PRIVATE_ENDPOINTS`; the proxy's allow list is
  built from the same value, so the two can never disagree.
- The fence harness, its recording site and its kit install are reused as they are.

## Gaps

- **How the proxy takes `AI_PRIVATE_ENDPOINTS`.** Smokescreen's flags are repeated per address
  (`--allow-address`), and Compose cannot split a comma list into flags. Step 1 confirms whether
  its config file or a small entrypoint is needed, by running the image, not from its README.
- **Unauthenticated clients and the ACL.** Smokescreen names a client by its TLS certificate.
  Without one, whether the ACL's `default` rule applies is confirmed in Step 1 by test.

## Steps and tasks

### Step 1 — the block holds (the founder can see it: the fence test's metadata line turns from "printed" to "refused")

- [ ] **T1.1** `apps/scheduler/tests/test_insight_network_live.py` · the metadata test asserts
  refused, by direct connect and through the proxy; a store and an unlisted private address are
  refused through the proxy; the peer test becomes "reachable through the proxy when listed,
  refused when not". **Run first on today's layout and seen failing** (the regression proof).
- [ ] **T1.2** `deploy/compose/compose.infra.yaml` · `ai: internal: true`; `ai-out:` new.
- [ ] **T1.3** `deploy/compose/nexovue.yaml`, `docker-compose.yaml`, and the overlays
  `nexovue.hosted.yaml`, `nexovue.quickstart.yaml`, `nexovue.build.yaml` where they need it ·
  service `ai-egress` on `[ai, ai-out]`, image per Question 1, read-only root, no volume, memory
  limit, health check; deny ranges `169.254.0.0/16`, `fe80::/10`, `fd00:ec2::254/128`,
  `100.100.100.200/32`; allow `AI_PRIVATE_ENDPOINTS` by address and port; `scheduler-insight`
  gets `AI_EGRESS_PROXY=http://ai-egress:4750` and `depends_on` it.
- [ ] **T1.4** `apps/scheduler/src/nexovue_scheduler/probes/http_check.py` · `perform(...,
  proxy=)`: sets `PROXY` and `HTTPPROXYTUNNEL` (a `CONNECT` for http and https alike, so the proxy
  judges both); the guard admits only the proxy's own address and port; the target is still
  judged locally before sending, as a second check. A proxy refusal maps to `address_refused`.
  Proved by `apps/scheduler/tests/test_http_check_proxy.py`, against a small `CONNECT` proxy
  fixture in the test.
- [ ] **T1.5** `insight/transport.py`, `settings.py` · `send` passes the proxy from
  `AI_EGRESS_PROXY`; the docstring says where the address is now judged. Proved by
  `test_insight_transport.py` additions.
- [ ] **T1.6** `apps/scheduler/tests/test_compose_ai_network_structure.py` · reads every layout:
  `ai` is internal, only `ai-egress` joins `ai-out`, and `scheduler-insight` has
  `AI_EGRESS_PROXY`. Seen failing before T1.2 and T1.3.

### Step 2 — the optional host allow-list (AC53)

- [ ] **T2.1** `ai-egress` reads `AI_EGRESS_ALLOWED_HOSTS`; empty means any public host. Proved
  by a fence test: with it set, a host not listed is refused and the listed provider works.

### Step 3 — words and records

- [ ] **T3.1** `docs/product/setup/ai.md`, `docs/product/setup/ai-suggested-fixes.md` (*Limiting
  where the AI process can reach*), `docs/changes/phase-2-live-test/release-notes-v0.2.0.md`
  (*Before you upgrade*): the block ships and is on; the allow-list is optional.
- [ ] **T3.2** `docs/operations/first-deploy.md` step 5: met by `ai-egress`, proved by the fence.
- [ ] **T3.3** F3 spec AC42 and its *What may be said* row; F5 spec AC53; F5 plan T4.11 ticked;
  F5 status decision 43 answered; F5 readiness B1 and F3 readiness S1 closed with the fence result.
- [ ] **T3.4** `docs/decisions/ADR-1xx-the-ai-role-reaches-out-only-through-a-proxy.md`: the
  internal network, the proxy as an infrastructure image beside Postgres (outside ADR-019's two),
  and the rejected alternatives below. ADR-040 gains a line pointing to it.

## New risks

- An IPv6-only provider, or a provider behind a CDN address the proxy treats as non-public, is
  refused. The fence makes one real public call; the docs say how to read the refusal.
- One more image to keep patched.

## Questions

1. **Which proxy image (open decision 43, yours).**
   - **(a) Stripe's Smokescreen, `ghcr.io/stripe/smokescreen:v1.0.1`.** Recommended. Built for
     exactly this: it resolves each name itself and refuses any address that is not publicly
     routable unless allowed; MIT licence; non-root; amd64 and arm64. The catch: v1.0.0 and
     v1.0.1 were both tagged on 25 Nov 2026, three days ago; before that the project had no
     release tags.
   - **(b) Squid (`ubuntu/squid`).** Mature and widely used, but it is a general cache: every
     private range, metadata address and IP-literal case must be written as rules by us, and a
     missed rule is an open door.
   - **(c) Smokescreen built from source at a pinned commit** into a small image of our own. Most
     control, but a third nexovue image and a build to maintain.
2. **The host allow-list's default.** Recommended: **off** (any public host), as spec D49 says,
   because providers are chosen by each organization at run time. The block on metadata and
   internal addresses is on either way.

Answers go to F5 `status.md` (decision 43) and the ADR (T3.4).

## Gates

- Founder approves this plan and answers Questions 1 and 2.
- Regression tests T1.1 and T1.6 seen failing before the fix.
- `just check` and `just insight-fence` pass; results recorded in `status.md`.
- `/review-diff` before handing back. Commit only on the founder's yes; push only on "push".

## Validation plan

`just check` once at the end, then `just insight-fence` on the merged install layout, which now
asserts: metadata refused (direct and through the proxy), stores refused, unlisted private
address refused, listed private endpoint reached, one public HTTPS host reached through the proxy.

## Rollout and rollback

Ships in `v0.2.0-rc.1` and is on for every install. Rollback is the previous release's compose
files; no data changes.

**Rejected:** a host firewall rule (iptables `DOCKER-USER`) written by the installer. It blocks
metadata, but only on Linux hosts run through our installer, outside Compose, and gives no
allow-list. Recorded in the ADR as a second layer an operator may add, and used on our own
server too.

## Outcome

To be filled after the work.

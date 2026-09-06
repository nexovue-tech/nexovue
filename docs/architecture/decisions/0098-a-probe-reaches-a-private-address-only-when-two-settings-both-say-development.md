# 0098 — A probe reaches a private address only when two settings both say development

- **Status:** Accepted
- **Date:** 2026-09-06
- **Extends:** ADR 0096, which built the browser flow's egress boundary, and the `ConnectGuard`
  that predates it. Neither is changed here; a second question is asked beside them.

## Context

`just otel-demo up` is the daily traffic source, and it publishes the OpenTelemetry Demo
storefront on the Docker host. A developer wanting door 2 to probe that storefront — so the
Synthetics screen has something on it that is not `example.com` — cannot have it, because a
Docker host is a **private address by construction** and the connect-time boundary refuses every
private address.

That boundary is the right one and is not in question. It exists because a synthetic check makes
our infrastructure fetch a URL the customer chose: unguarded, it is a request-forgery primitive.

Two things were true when this was looked at, and only one was expected:

**`checks/targets.py` needed no change at all.** It refuses eight internal hostnames and private
IP *literals*; `host.docker.internal` is neither, so the check is created today. Its own docstring
says it is "a fast, specific rejection" and "not the security boundary, and it cannot be" — the
fetch happens minutes later in another process, so a name resolving publicly at creation can
resolve privately at connect. Loosening it would have weakened security code for no gain.

**The whole block is at connect time**, in `probes/addresses.py`, which is exactly where the
design says it should be.

## Decision

**A probe may open a socket to a private address only when two independent settings both say
development, and even then never to loopback or link-local.**

Three parts, and each of them is load-bearing.

**A second predicate, not a wider one.** `is_publicly_routable` answers "can the public internet
reach this", and three suites exist to hold it to that answer — `test_connect_guard.py`,
`test_browser_egress_live.py` and `loopback_site.py` are all built on it meaning exactly what it
says. Widening it would have made those suites pass while proving less. So `may_connect_to`
is a new question asked beside it:

```python
def may_connect_to(address_text: str, *, private_allowed: bool) -> bool:
```

**Loopback and link-local stay refused at every setting.** This is the line worth defending.
Loopback is the worker's own container, and link-local is `169.254.169.254`, where every major
cloud serves instance credentials. Neither becomes safe to probe because somebody is developing —
a developer's laptop is exactly where a stolen role would be used. The single thing this opens is
a private network the developer is already inside.

**Two conditions, not one.** The scheduler's `allow_private_probe_targets` must be set *and* `env`
must be `development`. A staging or production deployment that inherited the variable from a
copied compose file still refuses, because the second condition is not something a variable can
turn on.

It is threaded explicitly — `ConnectGuard(private_allowed=…)`, `PublicAddressProxy(private_allowed=…)`,
and through `perform()` from the task — rather than read from the environment inside the boundary.
Every other collaborator of a probe is passed in, and a hidden module-level environment read is
the kind of thing that survives into production without anyone choosing it.

## Alternatives rejected

**Widen `is_publicly_routable`.** Rejected: it would silently defeat the suites whose entire
purpose is proving loopback is never contacted, and those suites would still have reported green.

**Allowlist the Docker host's literal address.** Rejected: the Docker Desktop gateway address is
not stable across versions or platforms, so the allowlist would be wrong on somebody's machine and
right on ours — the worst kind of security configuration.

**Put the demo on the dev stack's own compose network** so the storefront has a container name
rather than a private host address. Rejected: it would mean editing the pinned upstream clone's
networking, which the demo integration deliberately never does, and the address would still be
private.

**Loosen `checks/targets.py` as well.** Rejected as unnecessary once measured — see Context.

## Consequences

The Synthetics screen fills from the demo storefront on a developer's machine, and door 2 is
exercised against a real application rather than only against `example.com`.

The boundary is unchanged everywhere else, and that was verified rather than assumed. With
`ALLOW_PRIVATE_PROBE_TARGETS=true` and `ENV=development` set on the running scheduler:

```
http://host.docker.internal:8080/    status_code=200 up=True
http://127.0.0.1.nip.io:8080/        Target resolved to a non-public address
                                     and was not contacted: 127.0.0.1
http://169.254.169.254.nip.io/       Target resolved to a non-public address
                                     and was not contacted: 169.254.169.254
```

Both refusals are the rebinding case: a public name, accepted at creation, refused at the socket.

Suite counts, with the flag available and the existing tests unchanged: `test_connect_guard.py`
36 passed (27 before), `test_browser_egress_live.py` 7 passed, the scheduler's offline suite 188
passed.

The residual risk is a deployment that sets both. That is a deliberate act with a variable named
for what it does, in a file that says why, and it still cannot reach a metadata endpoint.

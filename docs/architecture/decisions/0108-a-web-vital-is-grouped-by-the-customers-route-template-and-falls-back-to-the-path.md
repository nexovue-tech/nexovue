# 0108 — A web vital is grouped by the customer's route template, and falls back to the path

- **Status:** Accepted
- **Date:** 2026-09-08
- **Applies:** the rule already written in `pagecontext.ts` — *"a route template is the customer's
  knowledge, not something derived from a path"* — to the RUM screen's breakdown.

## Context

P1-47's page breakdown groups `web_vitals` by `url_path`, which is the raw path the browser was on.
Read out of the dev store on 2026-09-07:

```
/cart/checkout/978ac453-aa9f-11f1-91c3-1e31e47ab720
```

**A real shop therefore produces one breakdown group per order.** Ten thousand orders is ten
thousand groups the screen must rank and truncate, so the breakdown degrades exactly where a
customer has enough traffic for it to matter. It is least useful at the size it was built for.

**The customer had already told us the answer and we were throwing it away.** The SDK takes a
`route` option — a template such as `/products/{id}` — and `pagecontext.ts` put it on the page-view
*span* only. `addPageContext`, which every web vital and error record goes through, set
`session.id`, `url.full` and `url.path` and nothing else. No route column existed anywhere
downstream.

**This was known and written down at the time.** `000030_web_vitals.up.sql`'s own header said so:

> `url_path` is a plain String and deliberately not LowCardinality… `pagecontext.ts` puts
> `http.route` only on the page-view span and only when the customer configured one, so a vital
> record carries no template to group by. A customer with per-id URLs therefore produces unbounded
> distinct values… Grouping paths into pages is a normalisation nothing in the system performs yet.

That paragraph was an accurate description of a gap, carried for a week without anybody closing it.

## Decision

**Carry the customer's own `http.route` through end to end, and group by it where there is one and
by `url_path` where there is not.** `if(route != '', route, url_path)`, in SQL, and the response
says which of the two a group is so the screen can show the difference — a template means "this
page", a path means "this URL", and a reader must not have to guess.

**`route` is `LowCardinality(String)` and sits at position 4 of the rollup's sort key**, immediately
before `url_path`:

```
(tenant_id, project_id, vital_name, route, url_path, device_class, country, minute)
```

Position first, because it is the expensive half. The read groups on route-then-path, so with
`route` ahead of `url_path` one group's rows are a single contiguous run of the key: every order id
under one template sits together, and an unrouted page's `route = ''` sorts first with `url_path`
ordering within it. Behind `url_path`, a routed page's rows scatter across every order it ever
served. It stays behind `vital_name` for 000030's own reason — a screen asks about one vital at a
time.

`LowCardinality` because a route table is bounded by the customer's own routes, tens to hundreds,
which is the very property that makes this change worth making; `url_path` is a plain String
precisely because it is unbounded, and that asymmetry is the decision in one line.

**Migrations 000030 and 000031 were rewritten in place rather than followed by a new pair.** Not
only rule 14: ClickHouse's `MODIFY ORDER BY` can only *append* a new column to the end of a sorting
key, so inserting at position 4 is not expressible as an ALTER and a rebuild was required either
way. Appending `route` after `minute` to dodge the rebuild would have been a sort key shaped by a
deploy window that does not exist.

## Alternatives considered

**Derive a template at ingest** — replace UUID- and digit-shaped segments with `{id}`. Rejected, and
it is the tempting one because it needs nothing from the customer. It invents data: a wrong guess is
invisible, permanent, and indistinguishable on the screen from a real template. `pagecontext.ts`
already carries the house rule against it.

**Group by route and drop the path entirely.** Rejected: a customer who never sets `route` would
lose the breakdown completely, where the fallback gives them a noisier one and a reason to configure
it.

**Blank `url_path` in the rollup row whenever a route is set**, which would genuinely bound the
rollup's row count rather than only the read. Not taken, and it is the strongest rejected option.
`config.route` is a single init-time string, so a customer who sets one static template across a
whole app would permanently lose every path with no way back. **The row count is therefore not
fixed, only the grouping** — that is written into 000031's header rather than claimed as solved.

**Appending `route` to the end of the sort key** to allow an `ALTER`. Rejected above.

## Consequences

**The rollup still mints one row per URL per minute.** A customer with per-id URLs and a route
configured gets a clean *screen* and an unchanged number of *rows*. Named here so nobody later reads
this ADR as having solved storage.

**`route` had to be added to the insert-deduplication token, and its absence was a live data-loss
defect.** `writer.go` derives ClickHouse's `insert_deduplication_token` from the rollup's sort-key
columns. With `route` in the key and not in the token, the minute a customer first configures a
template its rollup rows differ from the previous minute's in nothing else — same minute, path,
device, count, values — so the token collides and **the store silently drops the insert.** That is
data loss between receiver and storage, which Phase 1 forbids, and it is silent. The rule is now in
the docstring: the token must cover every column of the target's sort key.

**`route` is per-`init()`, so it does not work for a single-page app.** One string for the lifetime
of an init is correct for a server-rendered site where each page renders its own template into the
snippet. An SPA that inits once and navigates client-side stamps one template on every record of the
session. There is no per-navigation route API and adding one is its own work.

**The dev store's web vitals were dropped by the rebuild** — ten readings, re-derivable from the
`browser.web_vital` records still in `logs`, and nothing has re-derived them.

**No row in the evidence for this change came from a real browser.** The schema facts are read from
`system.columns`; every row was written by the Go writer under test or by a test's own insert.

# Bug: seven defects a hands-on QA pass found outside the feature it was testing

## Reported behavior

F18's QA pass (`docs/features/synthetic-checks/status.md`, *Validation*) drove the console in a
browser rather than through the suites. Ten defects in F18 were fixed inside that feature. Seven
more were in code F18 does not own, and are the subject of this record.

1. **A cold `docker compose up` leaves the API dead.** It exits during startup with
   `ObjectStoreError: could not prepare the bucket` and stays down, because nothing restarts it. A
   `docker restart` fixes it, which is why this has survived: every day's second start works.
2. **Every screen that loads data logs a React error.** "Cannot update a component (`RefreshControl`)
   while rendering a different component (`SyntheticsScreen`)", four times on one page.
3. **The unsaved-changes prompt cannot be used from a keyboard.** It appears, but focus stays behind
   it, Escape does nothing, and Tab walks the page underneath.
4. **Two `<main>` landmarks** on every settings screen.
5. **`dns` and `ttfb` are the same colour** to the eye in the five-phase chart, in both themes.
6. **Navigating away logs "Request timed out"** once per in-flight read, in a product whose own logs
   are the evidence when a customer says the console was slow.
7. **`just check` has one red test** that reads as a product failure and is not one:
   `test_metrics_live.py::test_the_window_delta_is_far_below_the_number_of_points_stored`.

## Reproduction

**1 — the API's cold start.** `docker compose -f deploy/compose/docker-compose.yaml up -d --build
--wait` on a stopped stack. Compose reports `Container nexovue-seaweedfs-1 Healthy`, then
`container nexovue-api-1 is unhealthy`. The API's own log, with the cause botocore recorded before
our own message replaced it:

```
2026-09-17T17:31:10.442634Z  Endpoint provider result: http://seaweedfs:8333/nexovue-dev
2026-09-17T17:31:10.445772Z  Exception received when sending HTTP request.
ConnectionRefusedError: [Errno 111] Connection refused
…
nexovue_api.data.objectstore.store.ObjectStoreError: could not prepare the bucket
2026-09-17T17:31:14.663878Z  Application startup failed. Exiting.
```

Three milliseconds between resolving the endpoint and the refusal: nothing was listening on 8333.
Probing the container by hand shows which ports answer what:

```
8333/status          -> HTTP/1.1 200 OK        (the S3 gateway, what the API calls)
8333/healthz         -> HTTP/1.1 200 OK
9333/cluster/status  -> HTTP/1.1 200 OK        (the master, what the health check probed)
```

**2 — the render-phase update.** Open any screen that has not been visited since load (a first
`useQuery` for a key) with the console open. Reproduced on `/synthetics`, `/synthetics/new` and four
components of one check's page; not reproducible on a warm screen, which is the tell.

**3 — the keyboard trap.** Edit a check, then click a sidebar link. The prompt appears. Read
`document.activeElement`: it is the sidebar link, outside the dialog. Escape leaves the prompt up.
Tab moves to the next sidebar link, behind the scrim.

**4 — the landmarks.** On `/settings/projects`, `document.querySelectorAll("main")` returns two
nested elements.

**5 — the colours.** The phase chart's legend, read from the computed styles rather than the eye:
`dns` is `oklch(0.6 0.102 200)` and `ttfb` is `oklch(0.6 0.109 180)` — 20° apart in hue at the same
lightness.

**6 — the mislabelled cancellation.** Navigate between screens with the console open: one
`WARN Request timed out` per read the old screen had in flight, at the instant of the click.

**7 — the red test.** `uv run pytest apps/api/tests/test_metrics_live.py -q`:

```
E  AssertionError: the window's total cannot be smaller than the largest single cumulative point
E  assert 543 >= 2423
```

## Root cause

1. `deploy/compose/compose.infra.yaml` probes `http://127.0.0.1:9333/cluster/status` — the **master**.
   The API calls the **S3 gateway** on 8333, which starts later in the same `weed server` process.
   `depends_on: seaweedfs: condition: service_healthy` therefore gates on a port the API never uses,
   and the gate passes before the port the API needs accepts a connection. The API's fail-fast is
   correct; the dependency it trusted was not.
2. `apps/web/src/app/layout/RefreshControl.tsx` — `useNewestData` subscribes to the query cache and
   calls `setNewest` on **every** event. React Query emits `added` synchronously, from inside
   `useQuery`'s first render, so a component rendering causes a state update in another component
   that is already mounted. React reports exactly that.
3. `apps/web/src/shared/components/UnsavedChanges.tsx` renders `role="alertdialog"` and
   `aria-modal="true"` and then does none of what those attributes promise: no focus move, no focus
   trap, no Escape. The attributes are a claim about behaviour that was never implemented.
4. `apps/web/src/shared/settings/SettingsLayout.tsx` uses `<main>` for the content column inside
   `AppShell`'s `<main>`. Two landmarks, one of which is a lie about the document's structure.
5. `packages/ui/tokens.css`, transcribed from `docs/design/design-system.md`: the six series hues are
   200, 180, 290, 265, 330 and 10. Three pairs sit 15–25° apart (200/180, 290/265, 10 against
   severity's 25) in a palette whose own rule is that a series must be distinct from every other.
   The ordering in `features/synthetics/phase-series.ts` spreads the first four apart, which is why
   this only became visible with the five-series chart F18 added.
6. `apps/web/src/shared/api-client.ts` reads `signal.aborted` to choose its message. That signal is
   aborted both by our own timeout and by the caller — React Query cancels a query when its
   component unmounts — and the two are different events. One is the server being slow; the other is
   the customer changing screens, which is not a failure at all.
7. `apps/api/tests/test_metrics_live.py` asserts the window delta is at least the largest single
   cumulative point. A cumulative counter's point is the total since its process started; the delta
   is what accrued inside the window. When the exporter started before the window, the delta is
   correctly smaller, so the assertion is arithmetic about the fixture's age rather than about the
   read. It passed while the k6 traffic was young and will fail for everyone from now on.

## Specification impact

`docs/design/design-system.md` owns the palette in defect 5 and is updated with it — the tokens file
is a transcription of that document, so changing one without the other would make the document
wrong. Nothing else changes a requirement.

## Regression test

Each fix is covered where its own layer is tested, and each was observed failing first:

| Defect | Test |
|---|---|
| 1 | `apps/api/tests/test_compose_dependencies.py` — a health check probes the port its dependants call, **and** a service that names another waits for it. |
| 2 | `apps/web/src/app/layout/RefreshControl.test.ts` — a child's first `useQuery` logs no React error. |
| 3 | `apps/web/src/shared/components/UnsavedChanges.test.ts` — focus, Escape, Tab wrapping and focus recapture, added **beside** the six AC19 navigation tests already in that file. |
| 4 | `apps/web/src/app/landmarks.test.ts` — one `<main>` in `src/`, in `AppShell`. |
| 5 | `packages/ui/scripts/verify-contrast.js` — every series pair at least 30° apart in hue or 0.10 in lightness, and every series 30° from the reserved hues. |
| 6 | `apps/web/src/shared/api-client.test.ts` — a caller's abort is not a timeout and logs nothing. |
| 7 | `apps/api/tests/test_metrics_live.py` — the delta is compared against a delta. |

## Fix plan and validation

See `status.md`.

## Status

See `status.md`.

# Status: website follow-ups

## Lifecycle status

Implemented, in review.

## Current task

None.

## Completed

- Scale needs at least 10 services: migration 0040, API refusal, billing screen.
- The browser guide gives the real npm and jsDelivr install.
- The README separates today's release from what is being built.
- `.nvmrc` matches CI and the images at 26.12.0.
- The flaky services test reads a settled window.
- `docs/self-hosting/docker.md`: the images, and Compose commands run against the published 0.1.0 kit (11 healthy
  containers, console 200, down and up again).

## Next action

Merge, then cut a release so the trace list fix and these reach self-hosted installs.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- The jsDelivr line pins SDK 0.1.2 and needs updating with each SDK release.
- `test_a_real_public_name_resolving_privately_is_refused_at_connect` needs live DNS within 5 seconds, and failed
  once on a slow resolver during this work. It passed when re-run, and is unchanged here.
- The Scale minimum and its price are provisional, like all of `plan_prices`.

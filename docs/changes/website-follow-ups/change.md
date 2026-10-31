# Change: website-follow-ups, what building the product website showed

## Classification

Migration and documentation.

## Current behavior and problem

Writing the product website against the code found five things to change, and one left open:

1. **Scale beats Pro at every size.** It is cheaper with more volume and retention, and any count can buy it.
2. **`docs/product/setup/browser.md` says the SDK cannot be installed.** It has been on npm since 0.1.1, and the
   guides are now public at nexovue.com/docs.
3. **The README's opening describes Phase 2 as present.** "It tells you what changed and why" is not in the release.
4. **`.nvmrc` says Node 26.7.0.** CI and both images use 26.12.0.
5. **Nothing tells a reader how to use the published images.** Two public images exist, and no guide names them or
   says why they cannot run alone.
6. **`test_one_service_counts_exactly_as_its_row_in_the_ranking` flaps under live traffic.** Its window ends in the
   minute still being written, so the two reads can differ by a request (421,944 against 421,943). Not fixed here:
   see Risks.

## Intended behavior

1. Scale needs at least 10 services (founder decision, 2026-10-31). The API refuses fewer, and the billing screen
   says so and starts the field at 10.
2. The guide gives the real `npm install` and a versioned jsDelivr script tag.
3. The README says what is in the release and points to the plan for what is next.
4. `.nvmrc` matches CI and the images.
5. `docs/self-hosting/docker.md` names the images and gives the commands, each run against the published 0.1.0 kit.

## Affected specifications and architecture

`docs/product/vision.md` §5 records the minimum. `min_committed_services` is a new column on `plan_prices`.

## Risks and rollback

The script tag pins a version, so it needs updating with each SDK release. A customer with fewer than 10 services
cannot choose Scale, which is the intent. Rollback is the migration's downgrade and reverting the commit.

Ending the live test's window five minutes ago was tried and reverted. CI's stack is minutes old, so that window held
two requests, and `test_the_projects_p95_is_a_merge_and_not_an_average_of_its_services` failed on 18.0 against 18.0.
A fix has to work on a stack with only fresh data, and be reproduced failing first.

## Plan and validation

The API and screen tests were written first and seen failing. `just check` runs the rest.

## Status

[`status.md`](status.md).

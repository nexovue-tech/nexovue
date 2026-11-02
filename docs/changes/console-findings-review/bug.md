# Bug: what a hands-on review of Findings found

## Reported behavior

Using the feature in the console on 2026-11-01 showed:

1. Service screens with panels refused, and after a few screens "Too many requests".
2. "fraud-detection is receiving 17% of its normal traffic" beside "Now 9.2" against a normal of 24, and "ad is failing 14%" on a finding that had recovered.
3. A snooze or mute shown once on the finding page, then gone from it and from the feed after a reload.
4. A React error about `use()` in the browser console on service screens.
5. "4.0 at worst, against 24 normally", with no unit.
6. A title in UTC above a chart in the browser's zone.
7. False traffic findings from `just detection-seed`.

## Reproduction

Measured on the dev stack, `/services/cart`, after about 20 seconds with no other page of ours open:

- The dev server sends 16 telemetry reads and aborts 8. React's `StrictMode` mounts twice, in development only. The server still counts the aborted ones: 4 of the 8 live reads got 429.
- A production build (`vite build`, `vite preview`) sends 8 reads, with no aborts and no 429. That is exactly the cap of 8.
- A screen left refreshing sends 8 reads every 10 seconds: 48 of the quota of 60 a minute.
- `checkout` sent 4.2 requests a minute against a seeded 21; `fraud-detection` 8.3 against 27; `email` 4.2 against 11.

## Root cause

1. `RateLimitMiddleware._serve` refused a read over the cap at once, as `policy.py` recorded as wrong for a burst. `LARGEST_SCREEN_FANOUT` still said 6 when the screen sends 8, so no test noticed. The quota of 60 fitted one refreshing screen and little else.
2. `_lead_sentence` in `detection/lifecycle.py` wrote the worst value in the present tense.
3. The finding carried only `muted`, which says whether it opened while muted, not the service's state now.
4. `lazyRouteComponent` in TanStack Router calls `use()` conditionally. React reports this in development builds only; none appeared on the production build. Not changed.
5. `describeSignal` and `bandSentence` used the bare rate.
6. `TrendChart` always drew in the browser's zone.
7. `generate` in `tools/detection-seed/seed.py` gave each service a random share of one peak.

## Fix

1. A read over the cap waits up to 5 seconds for a slot before it is refused. The cap of 8 is unchanged. The person's quota goes from 60 to 180 a minute, and `LARGEST_SCREEN_FANOUT` to 8.
2. Titles name the worst without claiming it is now: "has dropped to", "has failed up to", "has been up to". FR17 and the design are updated.
3. A finding carries `service_snoozed_until` and `service_muted_until`. The page offers End snooze and Unmute, and the feed says Snoozed or Muted.
4. No change: it is a development warning from the library.
5. Traffic readings say "requests a minute".
6. `TrendChart` takes a `zone`, and the finding page passes the project's.
7. The seed reads each service's rate over the last hour and centres its history there.

## Customer impact

No customer is affected today. A production screen had no room to spare: a second tab or an overlapping refresh would have lost panels. The false "is receiving" and the forgotten snooze would have misled someone during an incident.

## Specification impact

FR17's example and the design's title templates change. Stored titles change when a finding next updates; closed ones keep their old wording.

## Regression test

Seen failing before the fix:

- `test_a_read_past_the_cap_waits_for_a_slot_rather_than_being_refused`
- `test_the_quota_clears_a_screen_left_open_and_a_person_moving_around`
- `test_a_slowdown_and_a_traffic_fall_name_their_worst_without_claiming_it_is_now`
- `test_the_history_sits_at_each_service_s_live_rate` (could not be collected: `live_peaks` did not exist)
- `still says the service is snoozed after a reload, and offers to end it`
- `still says the service is muted after a reload, and offers to unmute`
- `says when the finding's service is snoozed or muted, after a reload too`
- `names requests a minute once in each sentence`

The chart's zone has no automated test; it was checked on the page.

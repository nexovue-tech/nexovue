# F6 detection simulation

A seeded, reproducible simulation of F6's anomaly detector on synthetic services. It answers one
question before the design is approved (spec FR9, D15): does the method stay inside the false-alarm
budget (at most 1 false finding per service per week) while still catching faults within D16's
*time to catch*?

It is a standalone uv project, apart from the root workspace, because it needs numpy and scipy and
no service does.

## What it simulates

- **Services.** 8 weeks per service, minute by minute: business, consumer or flat daily shapes,
  weekend levels of 15%, 50% or 100%, 2 to 3,000 requests a minute at peak, 14% noise per minute,
  level wander and drift, a daily batch spike in a fifth of services, 1 to 20 endpoints with Zipf
  shares, per-endpoint failure rates that drift (log-normal, one-hour memory), service-wide failure
  bursts (4× the rate, configurable start chance and mean length, or off), and latency as a
  64-bucket histogram per minute with a wandering cache-miss tail and 4× pauses.
- **The detector**, day by day from day 3, with findings and FR6's traffic shadow kept out of later
  pools (BR3). It is v2's method (`git log` for the design history) with these changes:
  - the dispersion of *failing more* and *slower* is fitted on ±180 minutes of the selected days;
  - **failing more learns the service's own burstiness.** A window that passes the beta-binomial
    tail test (with and without its worst minute), the minimum change, the request floor and the
    80% rule is unusual only if its failure rate without its worst minute is above the service's
    burst height H, or it has been above the tail threshold for longer than the service's burst
    length Lb. H is the highest window failure rate in the profile's pool among windows within a
    factor of two of the current request count (`--hq`, default 1.0); Lb is the longest of the
    pool's own runs above the threshold, in minutes, never below the sustain window (`--lq`,
    default 1.0). The same applies to each endpoint (FR2) at α/m;
  - **the burst tolerance is switched per service and per endpoint (D18).** Only a history with at
    least one excursion above the tail threshold per week of judged history (`--bursty-per-day`,
    default 1/7; the founder's D18 value is 1) counts as bursty and uses the test above; any other
    uses v2's test with no burst condition;
  - **a finding closes as "could no longer be judged" (D20)** when every signal not yet recovered
    has gone 6 consecutive hours without a judgeable window; its minutes stay out of the normal;
  - **losing traffic** is judged against the daily pool once 21 days of history exist, when the
    weekly days at that minute are excluded (AC13). Before 21 days it only keeps clearly low
    minutes out of the baseline (FR6).
- **Where the burst profile comes from** (`--profile`):
  - `spec`: ±180 minutes of the selected days, findings left out, as first specified;
  - `short-in`: the same, but keeping the minutes of findings that closed within 30 minutes;
  - `history`: the service's last 28 days, short findings kept;
  - `history-volume` (default): the same, with the height taken from windows of like volume.

  `--lb-cap` caps Lb below D16's time to catch (tried, off by default: it breaks the budget);
  `--no-switch --no-close-unjudged` give revision 1's method; `--no-loo-height` judges the height
  on the whole window; `--method plain` runs v2's failing test
  with no burst profile. `RESULTS.md` says why each choice was made.
- **Faults**, injected 3 times per service and fault into the judged weeks, scored against a no-fault
  reference of the same minutes: 2× and 5× slower, cache misses at 30%, failures +5 points, 30%
  failing, one endpoint (about 5% of traffic) failing completely, traffic down to 50% and 5%, a
  single bad minute (must open nothing), and burst-shaped faults (failure rate ×4 for 6 to 40
  minutes) that measure what adaptivity costs. A fault counts for AC36 when, in its first full window and against the
  reference, it moves its signal past the normal-sensitivity minimum change by at least 10% of
  that change (D19): slower ≥ 1.55× the reference p95; failing ≥ 6 extra failures and ≥ +2.2
  points (or, for the endpoint fault, ≥ 11 extra failures on the endpoint); traffic ≤ 45% of the
  reference. The summary also shows the old rule (at the minimum itself); a single bad minute
  counts only where the reference finds nothing unusual in the same minutes (AC2's "normal minutes
  around it").
- **AC13**: the same 6-hour outage on 2 of 3 past Tuesdays; the 4th Tuesday, optionally shifted,
  must open within time to catch, or join a finding already open on the same signal (BR2).

Service i of seed S draws from `np.random.default_rng([S, i, stream])`; bursts have their own stream,
so every burst setting shares the rest of each service.

## How to run

From the repository root:

```sh
# No-fault run, and faults at normal and high, for 100 services of seed 1
uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py run --seed 1 \
    --svc 0:100 --bursts 0.002:8 --faults normal,high --out out/s1.jsonl
# Bursts off
uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py run --seed 1 \
    --svc 0:100 --bursts off --out out/off_s1.jsonl
# AC13, with the 4th outage 60 minutes later
uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py ac13 --seed 1 \
    --services 25 --shift 60 --out out/ac13_s1.jsonl
# Markdown tables, means with 95% intervals across seeds
uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py summary out/*.jsonl
```

A service takes about 10 to 20 seconds; run seeds in parallel. `RESULTS.md` records the runs, their
exact commands and the tables.

Checks: `uv run ruff check tools/detection-sim`, `uv run ruff format --check tools/detection-sim`
(the root's ruff config), and
`uv run --project tools/detection-sim mypy --config-file tools/detection-sim/pyproject.toml tools/detection-sim/detect_sim.py`.

## What it proves, and what it does not

It shows how the method behaves on traffic shaped like the model: whether the budget holds, which
faults are caught and how fast, and what each choice costs. It does not show that the model is
like real traffic. The burst rate and length are guesses until the demo is measured
(`tools/detection-measure`, D15), which is why the results sweep them. It has no deploys, no
daylight-saving change, no late data, and no user actions (false alarm, new normal), and it does not
model the small-sample bias of a sampled p95. AC36 and AC37 on the demo remain the tests that count.

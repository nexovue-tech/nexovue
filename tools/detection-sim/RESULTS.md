# F6 detection simulation: results

## Revision 2: founder decisions D18 to D20 (2026-10-31)

Every table is `detect_sim.py summary` output: means across seeds 1 to 4 (100 services each, 4
history weeks, 4 judged weeks), with the 95% half-width of the mean; faults are 3 injections per
service and fault. Bursts are `rate:mean length`.

### Verdict

| Criterion | Result |
|---|---|
| Budget ≤ 1 at normal, whole sweep | **Fail at the harshest setting only**: 1.11 ± 0.28 at 1/500 × 8 min. Pass elsewhere: 0.80 at 1/500 × 4, 0.53 at 1/2000, 0.39 at 1/5000, 0.25 off. D18 as decided (1 a day): 2.11 and 1.43 at the two 1/500 settings (seed 1) |
| Same, daily mode (days 3 to 20) | **Fail at the harshest**: 1.23 ± 0.30; 0.85 at 1/500 × 4 and below |
| AC2: a single bad minute opens nothing | **Fail, narrowly**: 1 of 1,196 at normal with harsh bursts (0 with bursts off); 1 of 1,196 at high with bursts off. Diagnosis below |
| AC36 under D19, ≥ 80% within time to catch at normal | **Fail on two faults.** *Failing +5*: 0.61 with harsh bursts (0.90 off). *Traffic to 50%*: 0.77 harsh and 0.76 off, on only 46 eligible injections. Every other fault passes |
| AC13 opens, shift 0 and 60 | **Pass**: failing and traffic 1.000 at both shifts; slower 0.976 to 1.000 (the one late case is at slower's traffic floor) |

At the harshest bursts the budget and *failing +5* conflict: making the burst profile less tolerant
catches +5 in time but breaks the budget (Lb cap below: +5 0.75, budget 1.64).

### What this revision does, and its deviations

- **D18, per service and per endpoint.** A history is bursty when its 28-day burst-profile pool
  holds, per judged day, at least the threshold count of excursions above the beta-binomial tail
  threshold (runs of windows passing the tail test with and without their worst minute). Judged
  days are the days of the pool after the 3 learning days, which have no record; a service on day 3
  has none and is not bursty. Bursty histories use the earlier adaptive test (H the highest
  like-volume window rate, Lb the longest excursion, leave-one-out height); others use v2's test
  with the ±180-minute dispersion pool.
- **Deviation, D18's threshold: 1 excursion a week, not 1 a day** (`--bursty-per-day`, default
  1/7; `--bursty-per-day 1` is D18 as decided). Fix for the budget. At 1 a day only 5% to 18% of
  service-days and almost no endpoints count as bursty, so v2's test runs nearly everywhere: 2.11 at
  1/500 × 8 (seed 1). One excursion a week is the budget itself: a history that reaches it breaks
  the budget under v2's test. After: 1.11 ± 0.28. Before and after are in table A.
- **D19.** A fault counts for AC36 when its first full window, against the no-fault reference,
  moves its signal past the minimum change by at least 10% of that change: slower p95 ≥ 1.55× the
  reference; failing ≥ 6 extra failures and ≥ +2.2 points (the endpoint fault: that, or ≥ 11 extra
  failures on the endpoint); traffic ≤ 45% of the reference, with the signal judgeable. Tables show
  both rules.
- **D20, as interpreted.** A signal that has gone 6 hours (360 consecutive minutes) without a
  judgeable window stops holding its finding open. The finding closes when every signal has
  recovered or gone unjudged, with reason "could no longer be judged" if any went unjudged. Its
  minutes stay excluded. It fires 0.001 to 0.003 times per service-week in no-fault runs (table
  A, last column). It resolves the earlier AC13 miss (seed 2, service 10002 now opens at 6 to
  7 minutes).
- **AC13 scoring.** An outage that joins a finding already open on the same signal counts as
  caught (BR2). Two such cases at shift 0, both findings opened 4 and 21 minutes before 10:00 by
  ordinary bursts.
- **Tried and rejected for AC36 (the one fix): cap Lb** so that a longer excursion is still caught
  within time to catch (`--lb-cap`; Lb ≤ time to catch − 4 min, never under the sustain window).
  *Failing +5* at harsh bursts went from 0.61 to 0.75 (at 200 rpm or more 0.97), but the budget went
  from 1.11 to 1.64 and daily mode to 1.38. It is off by default.
- **AC2, not fixed.** Both spikes land on quiet services right after natural near-excursions (5
  failures in 4 minutes on a service near 0%; the endpoint case is the same). The leave-one-out
  test removes the spike and the rest still passes, and the spike completes the 80% rule. The
  reference itself is not unusual only because one of its minutes is clean. A leave-one-out 80%
  rule did not change either case, so it was not adopted.
- *Traffic to 50%*: an injection that thins traffic to 50% rarely has 80% of its minutes at 45%
  or below, so only 46 of 1,200 count under D19 and the share is noisy. AC36 should inject this
  fault at about 40%.

### Commands

From the repository root, `S="uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py"`,
seeds `s` 1 to 4; defaults are this revision's method:

```sh
$S run --seed $s --svc 0:100 --bursts 0.002:8 --faults normal,high --out r3/b500x8_s$s.jsonl
$S run --seed $s --svc 0:100 --bursts off --faults normal,high --out r3/off_s$s.jsonl
$S run --seed $s --svc 0:100 --bursts B --out r3/B_s$s.jsonl            # B in 0.002:4 0.0005:4 0.0002:4
$S ac13 --seed $s --services 25 --bursts 0.002:8 --shift H --out r3/ac13_shH_s$s.jsonl   # H in 0 60
# D18 as decided, seed 1 only, normal
$S run --seed 1 --svc 0:100 --sens normal --bursty-per-day 1 --bursts B --out r3a/B_s1.jsonl   # B in 0.002:8 (with --faults normal) off (with --faults normal) 0.002:4 0.0005:4
# Lb cap trial, normal
$S run --seed $s --svc 0:100 --sens normal --lb-cap --bursts B [--faults normal] --out r3cap/B_s$s.jsonl
$S summary r3/*.jsonl
```

### A. False findings per service-week, judged weeks 5 to 8

*Bursty services* and *bursty endpoints* are D18's class as the share of judged days (endpoints:
those ever judgeable); *closed unjudged* is D20's closures per service-week.

| method | bursts | sens | services | all | failing | ep | slower | traffic | <20 | 20-200 | >200 | worst | share with any | daily mode, days 3-20 | bursty services | bursty endpoints | closed unjudged |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/2000 x4min | normal | 400 (4 seeds) | 0.53 ± 0.15 | 0.16 ± 0.01 | 0.25 ± 0.10 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.27 ± 0.07 | 1.21 ± 0.27 | 3.25 | 0.56 ± 0.10 | 0.43 ± 0.15 | 0.25 ± 0.08 | 0.02 ± 0.01 | 0.001 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/2000 x4min | high | 400 (4 seeds) | 0.70 ± 0.16 | 0.22 ± 0.04 | 0.19 ± 0.10 | 0.10 ± 0.02 | 0.25 ± 0.08 | 0.00 ± 0.00 | 0.34 ± 0.07 | 1.64 ± 0.27 | 4.25 | 0.51 ± 0.10 | 0.41 ± 0.14 | 0.22 ± 0.08 | 0.02 ± 0.01 | 0.003 ± 0.003 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/2000 x4min | low | 400 (4 seeds) | 0.12 ± 0.03 | 0.00 ± 0.01 | 0.08 ± 0.02 | 0.03 ± 0.01 | 0.00 ± 0.00 | 0.01 ± 0.02 | 0.06 ± 0.03 | 0.26 ± 0.05 | 1.25 | 0.29 ± 0.07 | 0.08 ± 0.01 | 0.26 ± 0.10 | 0.03 ± 0.01 | 0.000 ± 0.000 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/500 x4min | normal | 400 (4 seeds) | 0.80 ± 0.22 | 0.18 ± 0.04 | 0.52 ± 0.16 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.04 ± 0.02 | 0.40 ± 0.04 | 1.83 ± 0.39 | 6.50 | 0.59 ± 0.12 | 0.85 ± 0.20 | 0.40 ± 0.08 | 0.07 ± 0.03 | 0.001 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/500 x4min | high | 400 (4 seeds) | 0.89 ± 0.14 | 0.22 ± 0.03 | 0.40 ± 0.12 | 0.10 ± 0.04 | 0.25 ± 0.08 | 0.01 ± 0.01 | 0.42 ± 0.03 | 2.11 ± 0.31 | 5.50 | 0.55 ± 0.10 | 0.83 ± 0.26 | 0.36 ± 0.09 | 0.05 ± 0.02 | 0.003 ± 0.003 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/500 x4min | low | 400 (4 seeds) | 0.24 ± 0.11 | 0.01 ± 0.01 | 0.21 ± 0.11 | 0.02 ± 0.02 | 0.00 ± 0.00 | 0.01 ± 0.01 | 0.11 ± 0.04 | 0.55 ± 0.21 | 3.25 | 0.36 ± 0.10 | 0.20 ± 0.03 | 0.43 ± 0.09 | 0.06 ± 0.02 | 0.000 ± 0.000 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/500 x8min | normal | 400 (4 seeds) | 1.11 ± 0.28 | 0.26 ± 0.05 | 0.74 ± 0.23 | 0.16 ± 0.06 | 0.03 ± 0.01 | 0.06 ± 0.01 | 0.60 ± 0.04 | 2.49 ± 0.50 | 10.50 | 0.66 ± 0.10 | 1.23 ± 0.30 | 0.46 ± 0.09 | 0.10 ± 0.04 | 0.002 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/500 x8min | high | 400 (4 seeds) | 1.04 ± 0.17 | 0.25 ± 0.03 | 0.50 ± 0.12 | 0.10 ± 0.03 | 0.25 ± 0.08 | 0.01 ± 0.01 | 0.49 ± 0.03 | 2.44 ± 0.28 | 7.25 | 0.57 ± 0.09 | 1.01 ± 0.28 | 0.39 ± 0.08 | 0.07 ± 0.02 | 0.003 ± 0.003 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/500 x8min | low | 400 (4 seeds) | 0.57 ± 0.18 | 0.04 ± 0.01 | 0.51 ± 0.18 | 0.03 ± 0.01 | 0.00 ± 0.00 | 0.04 ± 0.02 | 0.26 ± 0.05 | 1.31 ± 0.37 | 5.25 | 0.53 ± 0.07 | 0.58 ± 0.10 | 0.55 ± 0.08 | 0.13 ± 0.04 | 0.000 ± 0.000 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/5000 x4min | normal | 400 (4 seeds) | 0.39 ± 0.09 | 0.11 ± 0.03 | 0.13 ± 0.03 | 0.14 ± 0.06 | 0.03 ± 0.01 | 0.04 ± 0.01 | 0.20 ± 0.05 | 0.87 ± 0.18 | 3.00 | 0.51 ± 0.08 | 0.27 ± 0.08 | 0.11 ± 0.07 | 0.01 ± 0.01 | 0.001 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/5000 x4min | high | 400 (4 seeds) | 0.55 ± 0.09 | 0.15 ± 0.02 | 0.09 ± 0.03 | 0.10 ± 0.04 | 0.25 ± 0.07 | 0.00 ± 0.00 | 0.28 ± 0.05 | 1.29 ± 0.19 | 4.00 | 0.49 ± 0.12 | 0.24 ± 0.09 | 0.09 ± 0.07 | 0.01 ± 0.01 | 0.003 ± 0.003 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | 1/5000 x4min | low | 400 (4 seeds) | 0.08 ± 0.02 | 0.00 ± 0.00 | 0.04 ± 0.02 | 0.03 ± 0.01 | 0.00 ± 0.00 | 0.01 ± 0.01 | 0.05 ± 0.04 | 0.16 ± 0.04 | 1.00 | 0.22 ± 0.06 | 0.05 ± 0.02 | 0.16 ± 0.07 | 0.02 ± 0.01 | 0.000 ± 0.000 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | off | normal | 400 (4 seeds) | 0.25 ± 0.06 | 0.02 ± 0.02 | 0.05 ± 0.01 | 0.15 ± 0.06 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.12 ± 0.04 | 0.56 ± 0.16 | 4.50 | 0.45 ± 0.09 | 0.16 ± 0.06 | 0.02 ± 0.02 | 0.00 ± 0.01 | 0.001 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | off | high | 400 (4 seeds) | 0.41 ± 0.07 | 0.03 ± 0.02 | 0.02 ± 0.02 | 0.11 ± 0.04 | 0.25 ± 0.07 | 0.00 ± 0.00 | 0.23 ± 0.05 | 0.92 ± 0.08 | 4.75 | 0.47 ± 0.12 | 0.11 ± 0.03 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.003 ± 0.003 |
| adaptive history-volume h1 l1 loo switch0.142857 d20 | off | low | 400 (4 seeds) | 0.06 ± 0.01 | 0.00 ± 0.00 | 0.02 ± 0.01 | 0.04 ± 0.01 | 0.00 ± 0.00 | 0.01 ± 0.01 | 0.04 ± 0.03 | 0.13 ± 0.03 | 1.50 | 0.17 ± 0.03 | 0.04 ± 0.03 | 0.10 ± 0.06 | 0.02 ± 0.01 | 0.000 ± 0.000 |

D18 as decided (`--bursty-per-day 1`), seed 1:

| method | bursts | sens | services | all | failing | ep | slower | traffic | <20 | 20-200 | >200 | worst | share with any | daily mode, days 3-20 | bursty services | bursty endpoints | closed unjudged |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adaptive history-volume h1 l1 loo switch1 d20 | 1/2000 x4min | normal | 100 (1 seeds) | 0.61 | 0.32 | 0.20 | 0.13 | 0.03 | 0.04 | 0.29 | 1.61 | 6.00 | 0.49 | 0.42 | 0.00 | 0.00 | 0.003 |
| adaptive history-volume h1 l1 loo switch1 d20 | 1/500 x4min | normal | 100 (1 seeds) | 1.43 | 0.85 | 0.65 | 0.12 | 0.03 | 0.06 | 0.69 | 3.82 | 9.50 | 0.57 | 1.11 | 0.07 | 0.00 | 0.003 |
| adaptive history-volume h1 l1 loo switch1 d20 | 1/500 x8min | normal | 100 (1 seeds) | 2.11 | 1.02 | 1.13 | 0.13 | 0.03 | 0.06 | 1.24 | 5.38 | 11.25 | 0.62 | 1.58 | 0.16 | 0.00 | 0.003 |
| adaptive history-volume h1 l1 loo switch1 d20 | off | normal | 100 (1 seeds) | 0.24 | 0.04 | 0.06 | 0.13 | 0.03 | 0.04 | 0.09 | 0.66 | 4.50 | 0.41 | 0.14 | 0.00 | 0.00 | 0.003 |

Lb cap trial (`--lb-cap`), normal:

| method | bursts | sens | services | all | failing | ep | slower | traffic | <20 | 20-200 | >200 | worst | share with any | daily mode, days 3-20 | bursty services | bursty endpoints | closed unjudged |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adaptive history-volume h1 l1 loo switch0.142857 lbcap d20 | 1/2000 x4min | normal | 400 (4 seeds) | 0.55 ± 0.15 | 0.18 ± 0.02 | 0.25 ± 0.10 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.27 ± 0.07 | 1.25 ± 0.29 | 4.00 | 0.56 ± 0.10 | 0.43 ± 0.15 | 0.25 ± 0.08 | 0.02 ± 0.01 | 0.001 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 lbcap d20 | 1/500 x4min | normal | 400 (4 seeds) | 0.97 ± 0.29 | 0.35 ± 0.10 | 0.54 ± 0.18 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.04 ± 0.02 | 0.41 ± 0.05 | 2.29 ± 0.56 | 8.25 | 0.59 ± 0.12 | 0.90 ± 0.20 | 0.40 ± 0.08 | 0.07 ± 0.03 | 0.001 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 lbcap d20 | 1/500 x8min | normal | 400 (4 seeds) | 1.64 ± 0.43 | 0.84 ± 0.22 | 0.79 ± 0.25 | 0.16 ± 0.05 | 0.03 ± 0.01 | 0.06 ± 0.01 | 0.60 ± 0.04 | 3.97 ± 0.93 | 12.25 | 0.66 ± 0.10 | 1.38 ± 0.35 | 0.46 ± 0.09 | 0.10 ± 0.03 | 0.002 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 lbcap d20 | 1/5000 x4min | normal | 400 (4 seeds) | 0.39 ± 0.09 | 0.12 ± 0.03 | 0.13 ± 0.03 | 0.14 ± 0.06 | 0.03 ± 0.01 | 0.04 ± 0.01 | 0.20 ± 0.05 | 0.88 ± 0.18 | 3.00 | 0.51 ± 0.08 | 0.27 ± 0.08 | 0.11 ± 0.07 | 0.01 ± 0.01 | 0.001 ± 0.002 |
| adaptive history-volume h1 l1 loo switch0.142857 lbcap d20 | off | normal | 400 (4 seeds) | 0.25 ± 0.06 | 0.02 ± 0.02 | 0.05 ± 0.01 | 0.15 ± 0.06 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.12 ± 0.04 | 0.56 ± 0.16 | 4.50 | 0.45 ± 0.09 | 0.16 ± 0.06 | 0.02 ± 0.02 | 0.00 ± 0.01 | 0.001 ± 0.002 |

Lb cap trial, harsh bursts, normal (same columns as table B):

| fault | clear / past min / injected | caught | within | within, old rule | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|---|
| failing_plus5 | 625 / 662 / 1200 | 0.867 ± 0.022 | 0.754 ± 0.058 | 0.725 ± 0.050 | 9 | 0.266 ± 0.284 (n=30) | 0.624 ± 0.072 (n=328) | 0.968 ± 0.028 (n=267) | 0.844 ± 0.020 |
| endpoint_100pct | 493 / 519 / 960 | 0.929 ± 0.046 | 0.860 ± 0.073 | 0.827 ± 0.077 | 7 | 0.317 ± 0.103 (n=29) | 0.814 ± 0.084 (n=260) | 1.000 ± 0.000 (n=204) | 0.883 ± 0.073 |
| spike_1min | 1195 / 1195 / 1200 | 0.001 ± 0.003 | 0.001 ± 0.003 | 0.001 ± 0.003 | - | 0.002 ± 0.006 (n=563) | 0.000 ± 0.000 (n=360) | 0.000 ± 0.000 (n=272) | 0.001 ± 0.003 |

### B and D. Faults (D19 eligibility; part D split by D18 class)

#### Faults: adaptive history-volume h1 l1 loo switch0.142857 d20, bursts 1/500 x8min, high sensitivity

Injections clearly past the normal-sensitivity minimum (D19), unless marked old rule. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | clear / past min / injected | caught | within | within, old rule | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|---|
| slower_2x | 418 / 420 / 1200 | 0.944 ± 0.034 | 0.846 ± 0.072 | 0.847 ± 0.072 | 6 | - (n=0) | 0.808 ± 0.081 (n=172) | 0.876 ± 0.080 (n=246) | 0.846 ± 0.072 |
| slower_5x | 433 / 433 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=175) | 1.000 ± 0.000 (n=258) | 1.000 ± 0.000 |
| cache_30pct | 436 / 437 / 1200 | 0.996 ± 0.008 | 0.980 ± 0.022 | 0.980 ± 0.022 | 6 | - (n=0) | 0.964 ± 0.051 (n=192) | 0.991 ± 0.017 (n=244) | 0.980 ± 0.022 |
| failing_plus5 | 533 / 566 / 1200 | 0.783 ± 0.067 | 0.561 ± 0.090 | 0.541 ± 0.091 | 9 | 0.083 ± 0.265 (n=6) | 0.501 ± 0.060 (n=278) | 0.635 ± 0.127 (n=249) | 0.794 ± 0.016 |
| failing_30pct | 811 / 825 / 1200 | 0.999 ± 0.004 | 0.993 ± 0.008 | 0.992 ± 0.010 | 6 | 0.968 ± 0.034 (n=182) | 1.000 ± 0.000 (n=372) | 1.000 ± 0.000 (n=257) | 0.993 ± 0.008 |
| endpoint_100pct | 430 / 462 / 960 | 0.891 ± 0.074 | 0.819 ± 0.080 | 0.778 ± 0.112 | 6 | 0.000 ± 0.000 (n=7) | 0.702 ± 0.101 (n=233) | 1.000 ± 0.000 (n=190) | 0.841 ± 0.073 |
| traffic_50pct | 9 / 242 / 1200 | 1.000 ± 0.000 | 0.667 ± 0.750 | 0.892 ± 0.035 | 6 | - (n=0) | 0.667 ± 0.750 (n=9) | - (n=0) | 0.667 ± 0.750 |
| traffic_5pct | 481 / 481 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=221) | 1.000 ± 0.000 (n=260) | 1.000 ± 0.000 |
| spike_1min | 1196 / 1196 / 1200 | 0.000 ± 0.000 | 0.000 ± 0.000 | 0.000 ± 0.000 | - | 0.000 ± 0.000 (n=573) | 0.000 ± 0.000 (n=390) | 0.000 ± 0.000 (n=233) | 0.000 ± 0.000 |
| burst_like_6 | 293 / 336 / 1200 | 0.040 ± 0.030 | 0.040 ± 0.030 | 0.035 ± 0.029 | 6 | 0.000 (n=1) | 0.045 ± 0.024 (n=147) | 0.034 ± 0.045 (n=145) | 0.356 ± 0.106 |
| burst_like_10 | 299 / 336 / 1200 | 0.126 ± 0.069 | 0.123 ± 0.068 | 0.111 ± 0.062 | 7 | 0.000 (n=2) | 0.126 ± 0.068 (n=135) | 0.128 ± 0.090 (n=162) | 0.487 ± 0.028 |
| burst_like_20 | 289 / 336 / 1200 | 0.223 ± 0.071 | 0.101 ± 0.041 | 0.086 ± 0.030 | 16 | 0.167 ± 0.717 (n=4) | 0.107 ± 0.110 (n=132) | 0.098 ± 0.031 (n=153) | 0.506 ± 0.113 |
| burst_like_40 | 294 / 343 / 1200 | 0.312 ± 0.062 | 0.086 ± 0.039 | 0.074 ± 0.034 | 22 | - (n=0) | 0.110 ± 0.062 (n=150) | 0.067 ± 0.048 (n=144) | 0.487 ± 0.157 |

##### Part D: burst-shaped faults (failure rate x4), high

| length min | services | clear | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|---|
| 6 | bursty | 259 | 0.035 | 0.394 | 0.359 | 0.776 | 0.025 |
| 6 | not bursty | 34 | 0.088 | 0.088 | 0.000 | 0.000 | - |
| 6 | all | 293 | 0.041 | 0.358 | 0.317 | 0.686 | 0.025 |
| 10 | bursty | 252 | 0.115 | 0.556 | 0.440 | 0.587 | 0.081 |
| 10 | not bursty | 47 | 0.170 | 0.170 | 0.000 | 0.000 | - |
| 10 | all | 299 | 0.124 | 0.495 | 0.371 | 0.495 | 0.081 |
| 20 | bursty | 247 | 0.190 | 0.619 | 0.429 | 0.178 | 0.091 |
| 20 | not bursty | 42 | 0.405 | 0.405 | 0.000 | 0.000 | - |
| 20 | all | 289 | 0.221 | 0.588 | 0.367 | 0.152 | 0.091 |
| 40 | bursty | 258 | 0.310 | 0.694 | 0.384 | 0.008 | 0.000 |
| 40 | not bursty | 36 | 0.333 | 0.333 | 0.000 | 0.000 | - |
| 40 | all | 294 | 0.313 | 0.650 | 0.337 | 0.007 | 0.000 |

#### Faults: adaptive history-volume h1 l1 loo switch0.142857 d20, bursts 1/500 x8min, normal sensitivity

Injections clearly past the normal-sensitivity minimum (D19), unless marked old rule. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | clear / past min / injected | caught | within | within, old rule | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|---|
| slower_2x | 502 / 505 / 1200 | 0.927 ± 0.020 | 0.843 ± 0.026 | 0.844 ± 0.025 | 7 | - (n=0) | 0.788 ± 0.025 (n=250) | 0.895 ± 0.054 (n=252) | 0.843 ± 0.026 |
| slower_5x | 525 / 525 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | - (n=0) | 1.000 ± 0.000 (n=269) | 1.000 ± 0.000 (n=256) | 1.000 ± 0.000 |
| cache_30pct | 507 / 510 / 1200 | 0.998 ± 0.007 | 0.994 ± 0.011 | 0.994 ± 0.011 | 7 | - (n=0) | 0.988 ± 0.024 (n=255) | 1.000 ± 0.000 (n=252) | 0.994 ± 0.011 |
| failing_plus5 | 625 / 662 / 1200 | 0.852 ± 0.033 | 0.611 ± 0.094 | 0.589 ± 0.085 | 9 | 0.266 ± 0.284 (n=30) | 0.596 ± 0.092 (n=328) | 0.661 ± 0.148 (n=267) | 0.841 ± 0.025 |
| failing_30pct | 897 / 911 / 1200 | 0.999 ± 0.004 | 0.995 ± 0.006 | 0.991 ± 0.006 | 7 | 0.984 ± 0.024 (n=280) | 1.000 ± 0.000 (n=369) | 1.000 ± 0.000 (n=248) | 0.995 ± 0.006 |
| endpoint_100pct | 493 / 519 / 960 | 0.929 ± 0.046 | 0.860 ± 0.073 | 0.827 ± 0.077 | 7 | 0.317 ± 0.103 (n=29) | 0.814 ± 0.084 (n=260) | 1.000 ± 0.000 (n=204) | 0.883 ± 0.073 |
| traffic_50pct | 46 / 397 / 1200 | 0.875 ± 0.254 | 0.772 ± 0.203 | 0.582 ± 0.049 | 8 | 0.725 ± 0.110 (n=27) | 0.900 ± 0.318 (n=19) | - (n=0) | 0.772 ± 0.203 |
| traffic_5pct | 773 / 773 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | 1.000 ± 0.000 (n=134) | 1.000 ± 0.000 (n=386) | 1.000 ± 0.000 (n=253) | 1.000 ± 0.000 |
| spike_1min | 1196 / 1196 / 1200 | 0.001 ± 0.003 | 0.001 ± 0.003 | 0.001 ± 0.003 | - | 0.002 ± 0.006 (n=563) | 0.000 ± 0.000 (n=361) | 0.000 ± 0.000 (n=272) | 0.001 ± 0.003 |
| burst_like_6 | 349 / 399 / 1200 | 0.097 ± 0.026 | 0.097 ± 0.026 | 0.086 ± 0.030 | 8 | 0.375 ± 0.762 (n=8) | 0.085 ± 0.022 (n=178) | 0.105 ± 0.048 (n=163) | 0.486 ± 0.155 |
| burst_like_10 | 334 / 385 / 1200 | 0.101 ± 0.082 | 0.095 ± 0.075 | 0.090 ± 0.072 | 8 | 0.312 ± 0.381 (n=11) | 0.112 ± 0.121 (n=185) | 0.057 ± 0.034 (n=138) | 0.523 ± 0.099 |
| burst_like_20 | 355 / 408 / 1200 | 0.196 ± 0.065 | 0.089 ± 0.045 | 0.089 ± 0.045 | 15 | 0.067 ± 0.287 (n=9) | 0.102 ± 0.051 (n=189) | 0.069 ± 0.082 (n=157) | 0.529 ± 0.031 |
| burst_like_40 | 343 / 397 / 1200 | 0.420 ± 0.192 | 0.140 ± 0.095 | 0.129 ± 0.085 | 23 | 0.300 ± 0.446 (n=13) | 0.156 ± 0.103 (n=186) | 0.105 ± 0.111 (n=144) | 0.621 ± 0.101 |

##### Part D: burst-shaped faults (failure rate x4), normal

| length min | services | clear | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|---|
| 6 | bursty | 305 | 0.089 | 0.525 | 0.436 | 0.941 | 0.091 |
| 6 | not bursty | 44 | 0.159 | 0.159 | 0.000 | 0.045 | 0.000 |
| 6 | all | 349 | 0.097 | 0.479 | 0.381 | 0.828 | 0.090 |
| 10 | bursty | 294 | 0.085 | 0.575 | 0.490 | 0.765 | 0.080 |
| 10 | not bursty | 40 | 0.200 | 0.200 | 0.000 | 0.000 | - |
| 10 | all | 334 | 0.099 | 0.530 | 0.431 | 0.674 | 0.080 |
| 20 | bursty | 311 | 0.183 | 0.653 | 0.469 | 0.296 | 0.087 |
| 20 | not bursty | 44 | 0.273 | 0.273 | 0.000 | 0.000 | - |
| 20 | all | 355 | 0.194 | 0.606 | 0.411 | 0.259 | 0.087 |
| 40 | bursty | 296 | 0.419 | 0.804 | 0.385 | 0.010 | 0.333 |
| 40 | not bursty | 47 | 0.447 | 0.447 | 0.000 | 0.000 | - |
| 40 | all | 343 | 0.423 | 0.755 | 0.332 | 0.009 | 0.333 |

#### Faults: adaptive history-volume h1 l1 loo switch0.142857 d20, bursts off, high sensitivity

Injections clearly past the normal-sensitivity minimum (D19), unless marked old rule. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | clear / past min / injected | caught | within | within, old rule | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|---|
| slower_2x | 412 / 415 / 1200 | 0.928 ± 0.063 | 0.808 ± 0.086 | 0.810 ± 0.084 | 6 | - (n=0) | 0.795 ± 0.127 (n=171) | 0.820 ± 0.078 (n=241) | 0.808 ± 0.086 |
| slower_5x | 424 / 424 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=169) | 1.000 ± 0.000 (n=255) | 1.000 ± 0.000 |
| cache_30pct | 434 / 435 / 1200 | 0.996 ± 0.008 | 0.989 ± 0.013 | 0.989 ± 0.013 | 6 | - (n=0) | 0.974 ± 0.031 (n=183) | 1.000 ± 0.000 (n=251) | 0.989 ± 0.013 |
| failing_plus5 | 536 / 570 / 1200 | 0.943 ± 0.021 | 0.897 ± 0.032 | 0.859 ± 0.029 | 6 | 0.083 ± 0.265 (n=7) | 0.824 ± 0.098 (n=266) | 0.988 ± 0.014 (n=263) | 0.897 ± 0.032 |
| failing_30pct | 811 / 825 / 1200 | 0.999 ± 0.004 | 0.994 ± 0.007 | 0.993 ± 0.010 | 6 | 0.973 ± 0.034 (n=186) | 1.000 ± 0.000 (n=373) | 1.000 ± 0.000 (n=252) | 0.994 ± 0.007 |
| endpoint_100pct | 425 / 459 / 960 | 0.949 ± 0.023 | 0.880 ± 0.072 | 0.833 ± 0.097 | 6 | 0.250 ± 0.796 (n=7) | 0.803 ± 0.123 (n=224) | 1.000 ± 0.000 (n=194) | 0.880 ± 0.072 |
| traffic_50pct | 9 / 254 / 1200 | 1.000 ± 0.000 | 0.688 ± 0.753 | 0.904 ± 0.076 | 6 | - (n=0) | 0.688 ± 0.753 (n=9) | - (n=0) | 0.688 ± 0.753 |
| traffic_5pct | 477 / 477 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=234) | 1.000 ± 0.000 (n=243) | 1.000 ± 0.000 |
| spike_1min | 1199 / 1199 / 1200 | 0.001 ± 0.003 | 0.001 ± 0.003 | 0.001 ± 0.003 | - | 0.000 ± 0.000 (n=576) | 0.000 ± 0.000 (n=376) | 0.003 ± 0.011 (n=247) | 0.001 ± 0.003 |
| burst_like_6 | 291 / 337 / 1200 | 0.553 ± 0.042 | 0.553 ± 0.042 | 0.515 ± 0.029 | 6 | 1.000 (n=1) | 0.183 ± 0.107 (n=146) | 0.919 ± 0.041 (n=144) | 0.553 ± 0.042 |
| burst_like_10 | 295 / 334 / 1200 | 0.612 ± 0.139 | 0.612 ± 0.139 | 0.589 ± 0.131 | 6 | 0.000 (n=2) | 0.297 ± 0.069 (n=139) | 0.908 ± 0.073 (n=154) | 0.612 ± 0.139 |
| burst_like_20 | 284 / 330 / 1200 | 0.709 ± 0.120 | 0.687 ± 0.076 | 0.646 ± 0.068 | 6 | 0.000 ± 0.000 (n=2) | 0.385 ± 0.129 (n=133) | 0.976 ± 0.048 (n=149) | 0.687 ± 0.076 |
| burst_like_40 | 295 / 343 / 1200 | 0.773 ± 0.126 | 0.698 ± 0.153 | 0.657 ± 0.145 | 6 | - (n=0) | 0.471 ± 0.151 (n=140) | 0.916 ± 0.054 (n=155) | 0.698 ± 0.153 |

##### Part D: burst-shaped faults (failure rate x4), high

| length min | services | clear | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|---|
| 6 | not bursty | 291 | 0.553 | 0.553 | 0.000 | 0.000 | - |
| 6 | all | 291 | 0.553 | 0.553 | 0.000 | 0.000 | - |
| 10 | not bursty | 295 | 0.617 | 0.617 | 0.000 | 0.000 | - |
| 10 | all | 295 | 0.617 | 0.617 | 0.000 | 0.000 | - |
| 20 | not bursty | 284 | 0.715 | 0.715 | 0.000 | 0.000 | - |
| 20 | all | 284 | 0.715 | 0.715 | 0.000 | 0.000 | - |
| 40 | not bursty | 295 | 0.773 | 0.773 | 0.000 | 0.000 | - |
| 40 | all | 295 | 0.773 | 0.773 | 0.000 | 0.000 | - |

#### Faults: adaptive history-volume h1 l1 loo switch0.142857 d20, bursts off, normal sensitivity

Injections clearly past the normal-sensitivity minimum (D19), unless marked old rule. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | clear / past min / injected | caught | within | within, old rule | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|---|
| slower_2x | 502 / 505 / 1200 | 0.925 ± 0.037 | 0.831 ± 0.055 | 0.832 ± 0.054 | 7 | - (n=0) | 0.790 ± 0.035 (n=256) | 0.873 ± 0.107 (n=246) | 0.831 ± 0.055 |
| slower_5x | 528 / 528 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | - (n=0) | 1.000 ± 0.000 (n=279) | 1.000 ± 0.000 (n=249) | 1.000 ± 0.000 |
| cache_30pct | 506 / 508 / 1200 | 0.998 ± 0.007 | 0.996 ± 0.007 | 0.996 ± 0.007 | 7 | - (n=0) | 0.992 ± 0.015 (n=269) | 1.000 ± 0.000 (n=237) | 0.996 ± 0.007 |
| failing_plus5 | 626 / 662 / 1200 | 0.964 ± 0.017 | 0.899 ± 0.041 | 0.872 ± 0.024 | 7 | 0.292 ± 0.334 (n=31) | 0.873 ± 0.051 (n=329) | 0.996 ± 0.012 (n=266) | 0.899 ± 0.041 |
| failing_30pct | 897 / 911 / 1200 | 0.999 ± 0.004 | 0.996 ± 0.007 | 0.992 ± 0.009 | 7 | 0.987 ± 0.027 (n=279) | 1.000 ± 0.000 (n=378) | 1.000 ± 0.000 (n=240) | 0.996 ± 0.007 |
| endpoint_100pct | 491 / 517 / 960 | 0.968 ± 0.026 | 0.906 ± 0.075 | 0.878 ± 0.066 | 7 | 0.374 ± 0.146 (n=30) | 0.896 ± 0.105 (n=255) | 1.000 ± 0.000 (n=206) | 0.906 ± 0.075 |
| traffic_50pct | 46 / 399 / 1200 | 0.856 ± 0.228 | 0.755 ± 0.188 | 0.575 ± 0.032 | 8 | 0.725 ± 0.110 (n=27) | 0.858 ± 0.301 (n=19) | - (n=0) | 0.755 ± 0.188 |
| traffic_5pct | 773 / 773 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | 1.000 ± 0.000 (n=135) | 1.000 ± 0.000 (n=388) | 1.000 ± 0.000 (n=250) | 1.000 ± 0.000 |
| spike_1min | 1198 / 1198 / 1200 | 0.000 ± 0.000 | 0.000 ± 0.000 | 0.000 ± 0.000 | - | 0.000 ± 0.000 (n=564) | 0.000 ± 0.000 (n=368) | 0.000 ± 0.000 (n=266) | 0.000 ± 0.000 |
| burst_like_6 | 343 / 397 / 1200 | 0.583 ± 0.100 | 0.583 ± 0.100 | 0.511 ± 0.109 | 7 | 0.000 ± 0.000 (n=5) | 0.383 ± 0.164 (n=187) | 0.850 ± 0.069 (n=151) | 0.589 ± 0.096 |
| burst_like_10 | 331 / 378 / 1200 | 0.659 ± 0.123 | 0.659 ± 0.123 | 0.609 ± 0.124 | 8 | 0.125 ± 0.398 (n=10) | 0.461 ± 0.200 (n=180) | 0.952 ± 0.036 (n=141) | 0.663 ± 0.133 |
| burst_like_20 | 350 / 406 / 1200 | 0.776 ± 0.075 | 0.722 ± 0.088 | 0.665 ± 0.102 | 8 | 0.067 ± 0.287 (n=7) | 0.571 ± 0.198 (n=179) | 0.903 ± 0.049 (n=164) | 0.722 ± 0.088 |
| burst_like_40 | 343 / 399 / 1200 | 0.818 ± 0.053 | 0.710 ± 0.092 | 0.666 ± 0.057 | 8 | 0.433 ± 0.662 (n=11) | 0.538 ± 0.120 (n=176) | 0.928 ± 0.044 (n=156) | 0.710 ± 0.092 |

##### Part D: burst-shaped faults (failure rate x4), normal

| length min | services | clear | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|---|
| 6 | bursty | 24 | 0.750 | 0.833 | 0.083 | 0.292 | 0.857 |
| 6 | not bursty | 319 | 0.567 | 0.567 | 0.000 | 0.003 | 1.000 |
| 6 | all | 343 | 0.580 | 0.586 | 0.006 | 0.023 | 0.875 |
| 10 | bursty | 18 | 0.889 | 0.944 | 0.056 | 0.111 | 1.000 |
| 10 | not bursty | 313 | 0.642 | 0.642 | 0.000 | 0.000 | - |
| 10 | all | 331 | 0.656 | 0.659 | 0.003 | 0.006 | 1.000 |
| 20 | bursty | 20 | 0.950 | 0.950 | 0.000 | 0.000 | - |
| 20 | not bursty | 330 | 0.767 | 0.767 | 0.000 | 0.000 | - |
| 20 | all | 350 | 0.777 | 0.777 | 0.000 | 0.000 | - |
| 40 | bursty | 18 | 0.944 | 0.944 | 0.000 | 0.000 | - |
| 40 | not bursty | 325 | 0.812 | 0.812 | 0.000 | 0.000 | - |
| 40 | all | 343 | 0.819 | 0.819 | 0.000 | 0.000 | - |

### C. AC13 (bursts 1/500 × 8 min, normal)

| bursts | method | shift min | kind | judgeable / services | opened or joined | within time to catch | joined | median delay | excluded |
|---|---|---|---|---|---|---|---|---|---|
| 1/500 x8min | adaptive history-volume h1 l1 loo switch0.142857 d20 | 0 | failing_30pct | 100 / 100 | 1.000 | 1.000 | 2 | 7 | 1.000 |
| 1/500 x8min | adaptive history-volume h1 l1 loo switch0.142857 d20 | 0 | slower_5x | 85 / 100 | 1.000 | 0.976 | 2 | 7 | 0.999 |
| 1/500 x8min | adaptive history-volume h1 l1 loo switch0.142857 d20 | 0 | traffic_5pct | 100 / 100 | 1.000 | 1.000 | 2 | 7 | 1.000 |
| 1/500 x8min | adaptive history-volume h1 l1 loo switch0.142857 d20 | 60 | failing_30pct | 100 / 100 | 1.000 | 1.000 | 0 | 7 | 1.000 |
| 1/500 x8min | adaptive history-volume h1 l1 loo switch0.142857 d20 | 60 | slower_5x | 86 / 100 | 1.000 | 1.000 | 0 | 7 | 0.998 |
| 1/500 x8min | adaptive history-volume h1 l1 loo switch0.142857 d20 | 60 | traffic_5pct | 100 / 100 | 1.000 | 1.000 | 0 | 7 | 1.000 |

## Earlier results: revision 1, before D18 to D20

Kept as run. With the current file these runs need `--no-switch --no-close-unjudged` added to
their commands; spike scoring and the old eligibility rule are as described in this section.


Run 2026-10-31 with `detect_sim.py` in this folder (numpy 2.5.4, scipy 1.18.1, Python 3.13). Every
table is the output of `detect_sim.py summary`; means are across seeds 1 to 4, with the 95%
half-width of the mean (t distribution, 3 degrees of freedom). Each seed is 100 services
(`--svc 0:100`), 4 history weeks then 4 judged weeks; faults are 3 injections per service and fault.

### Verdict

| Criterion | Result |
|---|---|
| Budget ≤ 1 false finding per service-week at normal, whole burst sweep | **Pass** with the final candidate: worst 0.85 ± 0.24 (1/500, 8 min). The method as first specified **fails**: 1.67 ± 0.40 at 1/500 × 4 min, 2.84 ± 0.60 at × 8 min |
| Same, daily mode (days 3 to 20), harshest bursts | **Borderline fail**: 1.08 ± 0.24 (pre-fix 1.01 ± 0.23). With under 3 weeks of history the profile has seen few bursts |
| AC2: a single bad minute opens nothing | **Pass** with the final candidate: 0 of 2,394 at normal. The pre-fix method caught 0.1% to 0.4% |
| AC36: ≥ 80% within time to catch, every fault past the minimum, normal | **Fail** on two faults. *Failing +5 points*: 0.56 with harsh bursts (0.87 with bursts off). *Traffic to 50%*: 0.58 in every setting, unchanged from v2, because 50% is exactly the minimum. Everything else passes (2× slower 0.84, one endpoint 0.81) |
| AC13: the 4th Tuesday opens within time to catch, outage shifted 0 or 60 min | **Pass** for losing traffic (1.000 at both shifts; v2 failed at 60). Failing 0.990, slower 0.976 to 1.000; the misses are explained below |

**The revised method as specified does not work** (calibration table below): the ±180-minute pool
on 4 days holds about one excursion above the tail threshold, and the bursts that open findings are
excluded from it, so the pool never contains the bursts it should learn. It measured 2.72 against
2.76 for v2's method on the same 40 services. Adaptivity works only with the deviations below, and
it then **trades fault catching for quiet**: faults shaped like the service's own bursts are
mostly missed (part D), and *failing +5 points* misses AC36 when bursts are harsh.

### What the final candidate is, and how it deviates from the brief

Defaults of `detect_sim.py` (`--profile history-volume --loo-height --hq 1 --lq 1`):

1. **The burst profile comes from the service's last 28 days, not ±180 minutes of the selected
   days** (fix for the budget). Burstiness is a property of the service, not the hour; ±180 × 4
   days holds too few bursts to take any quantile from.
2. **The profile keeps the minutes of short findings** (closed within 30 minutes of opening,
   that is unusual for at most D16's 20 minutes). Without this a service that alerts on every burst
   never sees one in its pool. The normal (p₀, dispersion) still excludes every finding (BR3).
3. **H is the highest window rate and Lb the longest excursion** in those 28 days (`--hq 1 --lq 1`),
   outside the suggested 99.5th to 99.9th percentile: with about 5 excursions above the threshold in
   4 weeks, any lower quantile sits inside the burst distribution (calibration: 2.06 at 99.9th,
   0.81 at the maximum). The floor at the beta-binomial threshold is implicit, since the window
   must pass that test as well.
4. **H is taken from pool windows within a factor of two of the current request count** (fix for
   *failing +5*): quiet windows' rates are noisier and set H too high for busy hours. This moved
   *failing +5* within time to catch from 0.49 ± 0.09 to 0.56 ± 0.09 only.
5. **The height is judged without the window's worst minute** (fix for AC2): one bad minute inside
   a tolerated burst otherwise lifted the window above H.
6. **Excursion duration** is the run of consecutive windows above the threshold (tail test with and
   without the worst minute) plus w − 1 minutes; the run continues across days from the frozen
   per-minute record, as the live detector would store it. Learning days 0 to 2 have no record.

Scoring rules (not method):

- A fault is *past the minimum* when, in its first full window and against the no-fault reference,
  it moves its signal past the normal-sensitivity minimum with the signal judgeable (v2's rule).
  For losing traffic, judgeable now includes daily mode from day 21.
- *Time to catch* is D16's: 12 minutes when the no-fault rate at the fault is ≥ 200 requests a
  minute, 20 below; settling (2) and the sweep (1) are counted. Fault bands are that rate; false
  finding bands are the service's peak.
- A single bad minute counts only where the reference finds nothing unusual in the same minutes
  (AC2's "normal minutes around it"). Of the pre-fix catches examined, those the height fix did not
  remove had a real deviation starting within minutes in the reference; the spike only advanced it.

### Commands

From the repository root, with
`S="uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py"` and seed `s` in
1 to 4 (output paths were under `/tmp`):

```sh
# Pre-fix method (history profile, whole-window height): all three sensitivities, faults at normal and high
$S run --seed $s --svc 0:100 --profile history --no-loo-height --bursts 0.002:8 --faults normal,high --out runs/hist_b500x8_s$s.jsonl
$S run --seed $s --svc 0:100 --profile history --no-loo-height --bursts off --faults normal,high --out runs/hist_off_s$s.jsonl
$S run --seed $s --svc 0:100 --profile history --no-loo-height --bursts 0.002:4 --out runs/hist_0.002x4_s$s.jsonl   # and 0.0005:4, 0.0002:4
# As first specified, normal only
$S run --seed $s --svc 0:100 --sens normal --profile spec --no-loo-height --hq 0.999 --lq 0.9 --bursts B --out runs/spec_B_s$s.jsonl   # B in off, 0.002:8, 0.002:4, 0.0005:4, 0.0002:4
# Final candidate (the defaults)
$S run --seed $s --svc 0:100 --sens normal --bursts 0.002:8 --faults normal --out fix/vol_b500x8_s$s.jsonl
$S run --seed $s --svc 0:100 --sens normal --bursts off --faults normal --out fix/vol_off_s$s.jsonl
$S run --seed $s --svc 0:100 --sens normal --bursts B --out fix/vol_B_s$s.jsonl                  # B in 0.002:4, 0.0005:4, 0.0002:4
$S run --seed $s --svc 0:100 --sens high,low --bursts 0.002:8 --out fix/vol_hl_b500x8_s$s.jsonl
# AC13, 25 services per seed and kind, shift 0 and 60, with and without the two fixes
$S ac13 --seed $s --services 25 --bursts 0.002:8 --shift 60 --out fix/ac13_sh60_s$s.jsonl
# Calibration: seed 101, normal only; P in spec, short-in, history; and --method plain
$S run --seed 101 --svc 0:40 --sens normal --bursts 0.002:4 --profile P --no-loo-height --hq 0.999 --lq 0.9 --out cal/P.jsonl
$S summary runs/*.jsonl fix/*.jsonl
```

Bursts are `rate:mean length`: 0.0002 is 1 in 5,000 minutes, 0.0005 1 in 2,000, 0.002 1 in 500.
The pre-fix runs scored spikes before the AC2 rule above, so their spike rows count every new
unusual window.

### A. False findings per service-week

All 400 services per row; *worst* is the worst single service; *daily mode* is days 3 to 20 at the
same sensitivity. "history h1 l1" is the pre-fix method; "spec" is the method as first specified.

| method | bursts | sens | services | all | failing | ep | slower | traffic | <20 | 20-200 | >200 | worst | share with any | daily mode, days 3-20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adaptive history h1 l1 | 1/2000 x4min | normal | 400 (4 seeds) | 0.40 ± 0.13 | 0.10 ± 0.02 | 0.16 ± 0.08 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.21 ± 0.05 | 0.91 ± 0.25 | 3.50 | 0.52 ± 0.11 | 0.37 ± 0.12 |
| adaptive history h1 l1 | 1/2000 x4min | high | 400 (4 seeds) | 0.56 ± 0.15 | 0.13 ± 0.08 | 0.11 ± 0.06 | 0.10 ± 0.02 | 0.25 ± 0.07 | 0.00 ± 0.00 | 0.28 ± 0.10 | 1.30 ± 0.24 | 3.50 | 0.49 ± 0.12 | 0.32 ± 0.10 |
| adaptive history h1 l1 | 1/2000 x4min | low | 400 (4 seeds) | 0.10 ± 0.03 | 0.00 ± 0.01 | 0.07 ± 0.03 | 0.03 ± 0.01 | 0.00 ± 0.00 | 0.01 ± 0.02 | 0.05 ± 0.01 | 0.23 ± 0.05 | 1.00 | 0.26 ± 0.04 | 0.08 ± 0.02 |
| adaptive history h1 l1 | 1/500 x4min | normal | 400 (4 seeds) | 0.57 ± 0.15 | 0.14 ± 0.03 | 0.31 ± 0.09 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.04 ± 0.02 | 0.27 ± 0.05 | 1.32 ± 0.29 | 5.25 | 0.57 ± 0.11 | 0.71 ± 0.21 |
| adaptive history h1 l1 | 1/500 x4min | high | 400 (4 seeds) | 0.65 ± 0.11 | 0.14 ± 0.03 | 0.21 ± 0.06 | 0.10 ± 0.03 | 0.25 ± 0.08 | 0.00 ± 0.01 | 0.31 ± 0.08 | 1.55 ± 0.19 | 4.75 | 0.51 ± 0.11 | 0.59 ± 0.19 |
| adaptive history h1 l1 | 1/500 x4min | low | 400 (4 seeds) | 0.19 ± 0.09 | 0.02 ± 0.02 | 0.16 ± 0.08 | 0.02 ± 0.02 | 0.00 ± 0.00 | 0.01 ± 0.01 | 0.09 ± 0.04 | 0.43 ± 0.17 | 3.00 | 0.34 ± 0.08 | 0.18 ± 0.03 |
| adaptive history h1 l1 | 1/500 x8min | normal | 400 (4 seeds) | 0.80 ± 0.24 | 0.19 ± 0.04 | 0.48 ± 0.16 | 0.16 ± 0.06 | 0.03 ± 0.01 | 0.05 ± 0.01 | 0.40 ± 0.09 | 1.82 ± 0.40 | 7.75 | 0.62 ± 0.10 | 1.01 ± 0.23 |
| adaptive history h1 l1 | 1/500 x8min | high | 400 (4 seeds) | 0.76 ± 0.17 | 0.17 ± 0.03 | 0.28 ± 0.12 | 0.11 ± 0.03 | 0.25 ± 0.07 | 0.00 ± 0.01 | 0.35 ± 0.07 | 1.81 ± 0.21 | 5.25 | 0.53 ± 0.11 | 0.74 ± 0.20 |
| adaptive history h1 l1 | 1/500 x8min | low | 400 (4 seeds) | 0.43 ± 0.14 | 0.04 ± 0.01 | 0.37 ± 0.15 | 0.03 ± 0.01 | 0.00 ± 0.00 | 0.03 ± 0.01 | 0.19 ± 0.06 | 0.99 ± 0.23 | 4.00 | 0.50 ± 0.07 | 0.52 ± 0.07 |
| adaptive history h1 l1 | 1/5000 x4min | normal | 400 (4 seeds) | 0.30 ± 0.07 | 0.06 ± 0.01 | 0.08 ± 0.02 | 0.15 ± 0.06 | 0.03 ± 0.01 | 0.04 ± 0.01 | 0.17 ± 0.05 | 0.66 ± 0.10 | 2.25 | 0.48 ± 0.09 | 0.24 ± 0.06 |
| adaptive history h1 l1 | 1/5000 x4min | high | 400 (4 seeds) | 0.46 ± 0.11 | 0.07 ± 0.03 | 0.06 ± 0.02 | 0.10 ± 0.03 | 0.25 ± 0.07 | 0.00 ± 0.00 | 0.25 ± 0.06 | 1.05 ± 0.19 | 3.25 | 0.48 ± 0.13 | 0.21 ± 0.07 |
| adaptive history h1 l1 | 1/5000 x4min | low | 400 (4 seeds) | 0.07 ± 0.02 | 0.00 ± 0.00 | 0.03 ± 0.02 | 0.03 ± 0.01 | 0.00 ± 0.00 | 0.01 ± 0.01 | 0.04 ± 0.03 | 0.15 ± 0.03 | 1.00 | 0.20 ± 0.05 | 0.04 ± 0.02 |
| adaptive history h1 l1 | off | normal | 400 (4 seeds) | 0.22 ± 0.07 | 0.01 ± 0.01 | 0.04 ± 0.02 | 0.15 ± 0.06 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.12 ± 0.04 | 0.50 ± 0.14 | 3.25 | 0.42 ± 0.12 | 0.14 ± 0.05 |
| adaptive history h1 l1 | off | high | 400 (4 seeds) | 0.38 ± 0.07 | 0.01 ± 0.01 | 0.01 ± 0.01 | 0.11 ± 0.04 | 0.25 ± 0.07 | 0.00 ± 0.00 | 0.23 ± 0.06 | 0.84 ± 0.10 | 3.50 | 0.47 ± 0.12 | 0.10 ± 0.03 |
| adaptive history h1 l1 | off | low | 400 (4 seeds) | 0.06 ± 0.00 | 0.00 ± 0.00 | 0.01 ± 0.01 | 0.04 ± 0.01 | 0.00 ± 0.00 | 0.01 ± 0.01 | 0.03 ± 0.02 | 0.12 ± 0.02 | 1.50 | 0.16 ± 0.02 | 0.04 ± 0.03 |
| adaptive history-volume h1 l1 loo | 1/2000 x4min | normal | 400 (4 seeds) | 0.42 ± 0.13 | 0.12 ± 0.02 | 0.18 ± 0.09 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.21 ± 0.06 | 0.97 ± 0.26 | 3.25 | 0.53 ± 0.10 | 0.39 ± 0.12 |
| adaptive history-volume h1 l1 loo | 1/500 x4min | normal | 400 (4 seeds) | 0.61 ± 0.15 | 0.15 ± 0.05 | 0.35 ± 0.09 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.04 ± 0.01 | 0.27 ± 0.04 | 1.43 ± 0.28 | 5.50 | 0.57 ± 0.10 | 0.76 ± 0.21 |
| adaptive history-volume h1 l1 loo | 1/500 x8min | normal | 400 (4 seeds) | 0.85 ± 0.24 | 0.22 ± 0.04 | 0.51 ± 0.18 | 0.16 ± 0.06 | 0.03 ± 0.01 | 0.05 ± 0.01 | 0.41 ± 0.08 | 1.96 ± 0.47 | 6.50 | 0.63 ± 0.11 | 1.08 ± 0.24 |
| adaptive history-volume h1 l1 loo | 1/500 x8min | high | 400 (4 seeds) | 0.85 ± 0.19 | 0.22 ± 0.03 | 0.34 ± 0.13 | 0.11 ± 0.03 | 0.25 ± 0.07 | 0.01 ± 0.01 | 0.37 ± 0.05 | 2.04 ± 0.28 | 5.75 | 0.54 ± 0.11 | 0.83 ± 0.21 |
| adaptive history-volume h1 l1 loo | 1/500 x8min | low | 400 (4 seeds) | 0.46 ± 0.15 | 0.04 ± 0.00 | 0.41 ± 0.15 | 0.03 ± 0.01 | 0.00 ± 0.00 | 0.03 ± 0.02 | 0.20 ± 0.05 | 1.09 ± 0.30 | 4.00 | 0.50 ± 0.05 | 0.54 ± 0.07 |
| adaptive history-volume h1 l1 loo | 1/5000 x4min | normal | 400 (4 seeds) | 0.32 ± 0.07 | 0.08 ± 0.01 | 0.09 ± 0.01 | 0.14 ± 0.06 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.18 ± 0.05 | 0.71 ± 0.12 | 2.25 | 0.49 ± 0.10 | 0.25 ± 0.08 |
| adaptive history-volume h1 l1 loo | off | normal | 400 (4 seeds) | 0.23 ± 0.07 | 0.01 ± 0.01 | 0.04 ± 0.02 | 0.15 ± 0.06 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.12 ± 0.04 | 0.51 ± 0.14 | 3.25 | 0.43 ± 0.11 | 0.15 ± 0.06 |
| adaptive spec h0.999 l0.9 | 1/2000 x4min | normal | 400 (4 seeds) | 0.65 ± 0.19 | 0.34 ± 0.10 | 0.23 ± 0.07 | 0.14 ± 0.06 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.31 ± 0.08 | 1.51 ± 0.37 | 6.00 | 0.56 ± 0.10 | 0.47 ± 0.17 |
| adaptive spec h0.999 l0.9 | 1/500 x4min | normal | 400 (4 seeds) | 1.67 ± 0.40 | 1.16 ± 0.23 | 0.68 ± 0.21 | 0.14 ± 0.05 | 0.03 ± 0.01 | 0.04 ± 0.02 | 0.66 ± 0.16 | 4.03 ± 0.79 | 12.75 | 0.61 ± 0.11 | 1.24 ± 0.28 |
| adaptive spec h0.999 l0.9 | 1/500 x8min | normal | 400 (4 seeds) | 2.84 ± 0.60 | 2.14 ± 0.35 | 1.07 ± 0.38 | 0.15 ± 0.06 | 0.03 ± 0.01 | 0.06 ± 0.01 | 1.26 ± 0.35 | 6.72 ± 1.10 | 17.25 | 0.67 ± 0.11 | 1.87 ± 0.39 |
| adaptive spec h0.999 l0.9 | 1/5000 x4min | normal | 400 (4 seeds) | 0.40 ± 0.09 | 0.15 ± 0.04 | 0.13 ± 0.02 | 0.14 ± 0.06 | 0.03 ± 0.01 | 0.04 ± 0.01 | 0.20 ± 0.04 | 0.92 ± 0.18 | 3.50 | 0.51 ± 0.09 | 0.28 ± 0.07 |
| adaptive spec h0.999 l0.9 | off | normal | 400 (4 seeds) | 0.25 ± 0.07 | 0.02 ± 0.02 | 0.05 ± 0.01 | 0.15 ± 0.06 | 0.03 ± 0.01 | 0.03 ± 0.01 | 0.12 ± 0.04 | 0.56 ± 0.17 | 4.50 | 0.45 ± 0.10 | 0.15 ± 0.06 |

### B and D. Faults

Final candidate at normal; part D (burst-shaped faults, failure rate ×4) follows each. *v2 method
within* is the same injection judged without the burst profile, so the difference is adaptivity's
cost.

##### Faults: adaptive history-volume h1 l1 loo, bursts 1/500 x8min, normal sensitivity

Injections past the normal-sensitivity minimum. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | past min / injected | caught | within | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|
| slower_2x | 505 / 1200 | 0.928 ± 0.019 | 0.844 ± 0.025 | 7 | - (n=0) | 0.791 ± 0.027 (n=253) | 0.895 ± 0.054 (n=252) | 0.844 ± 0.025 |
| slower_5x | 525 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | - (n=0) | 1.000 ± 0.000 (n=269) | 1.000 ± 0.000 (n=256) | 1.000 ± 0.000 |
| cache_30pct | 510 / 1200 | 0.998 ± 0.007 | 0.994 ± 0.011 | 7 | - (n=0) | 0.988 ± 0.024 (n=255) | 1.000 ± 0.000 (n=255) | 0.994 ± 0.011 |
| failing_plus5 | 662 / 1200 | 0.799 ± 0.005 | 0.564 ± 0.093 | 10 | 0.137 ± 0.203 (n=55) | 0.560 ± 0.090 (n=340) | 0.651 ± 0.130 (n=267) | 0.806 ± 0.038 |
| failing_30pct | 911 / 1200 | 0.998 ± 0.004 | 0.991 ± 0.006 | 7 | 0.971 ± 0.024 (n=294) | 1.000 ± 0.000 (n=369) | 1.000 ± 0.000 (n=248) | 0.991 ± 0.006 |
| endpoint_100pct | 519 / 960 | 0.884 ± 0.058 | 0.807 ± 0.091 | 7 | 0.227 ± 0.133 (n=46) | 0.765 ± 0.116 (n=269) | 1.000 ± 0.000 (n=204) | 0.849 ± 0.076 |
| traffic_50pct | 397 / 1200 | 0.876 ± 0.017 | 0.582 ± 0.049 | 10 | 0.606 ± 0.133 (n=77) | 0.730 ± 0.109 (n=175) | 0.378 ± 0.129 (n=145) | 0.582 ± 0.049 |
| traffic_5pct | 773 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | 1.000 ± 0.000 (n=134) | 1.000 ± 0.000 (n=386) | 1.000 ± 0.000 (n=253) | 1.000 ± 0.000 |
| spike_1min | 1196 / 1200 | 0.000 ± 0.000 | 0.000 ± 0.000 | - | 0.000 ± 0.000 (n=563) | 0.000 ± 0.000 (n=361) | 0.000 ± 0.000 (n=272) | 0.000 ± 0.000 |
| burst_like_6 | 399 / 1200 | 0.081 ± 0.020 | 0.081 ± 0.020 | 8 | 0.133 ± 0.260 (n=14) | 0.068 ± 0.025 (n=209) | 0.092 ± 0.034 (n=176) | 0.415 ± 0.121 |
| burst_like_10 | 385 / 1200 | 0.090 ± 0.072 | 0.085 ± 0.066 | 8 | 0.217 ± 0.331 (n=19) | 0.098 ± 0.101 (n=207) | 0.055 ± 0.032 (n=159) | 0.472 ± 0.112 |
| burst_like_20 | 408 / 1200 | 0.164 ± 0.082 | 0.076 ± 0.051 | 17 | 0.050 ± 0.159 (n=18) | 0.082 ± 0.057 (n=220) | 0.071 ± 0.063 (n=170) | 0.491 ± 0.066 |
| burst_like_40 | 397 / 1200 | 0.360 ± 0.174 | 0.099 ± 0.066 | 24 | 0.169 ± 0.220 (n=20) | 0.097 ± 0.092 (n=214) | 0.095 ± 0.077 (n=163) | 0.549 ± 0.090 |

###### Part D: burst-shaped faults (failure rate x4), normal

| length min | past min | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|
| 6 | 399 | 0.080 | 0.411 | 0.331 | 0.805 | 0.078 |
| 10 | 385 | 0.088 | 0.483 | 0.395 | 0.668 | 0.074 |
| 20 | 408 | 0.164 | 0.578 | 0.414 | 0.289 | 0.102 |
| 40 | 397 | 0.360 | 0.708 | 0.348 | 0.020 | 0.375 |

##### Faults: adaptive history-volume h1 l1 loo, bursts off, normal sensitivity

Injections past the normal-sensitivity minimum. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | past min / injected | caught | within | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|
| slower_2x | 505 / 1200 | 0.926 ± 0.036 | 0.832 ± 0.054 | 7 | - (n=0) | 0.792 ± 0.037 (n=258) | 0.873 ± 0.107 (n=247) | 0.832 ± 0.054 |
| slower_5x | 528 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | - (n=0) | 1.000 ± 0.000 (n=279) | 1.000 ± 0.000 (n=249) | 1.000 ± 0.000 |
| cache_30pct | 508 / 1200 | 0.998 ± 0.007 | 0.996 ± 0.007 | 7 | - (n=0) | 0.992 ± 0.015 (n=270) | 1.000 ± 0.000 (n=238) | 0.996 ± 0.007 |
| failing_plus5 | 662 / 1200 | 0.937 ± 0.022 | 0.871 ± 0.026 | 7 | 0.334 ± 0.181 (n=55) | 0.856 ± 0.040 (n=341) | 0.996 ± 0.012 (n=266) | 0.872 ± 0.024 |
| failing_30pct | 911 / 1200 | 0.998 ± 0.004 | 0.992 ± 0.009 | 7 | 0.974 ± 0.032 (n=293) | 1.000 ± 0.000 (n=378) | 1.000 ± 0.000 (n=240) | 0.992 ± 0.009 |
| endpoint_100pct | 517 / 960 | 0.952 ± 0.033 | 0.870 ± 0.067 | 7 | 0.353 ± 0.131 (n=46) | 0.863 ± 0.104 (n=265) | 1.000 ± 0.000 (n=206) | 0.878 ± 0.066 |
| traffic_50pct | 399 / 1200 | 0.892 ± 0.035 | 0.575 ± 0.032 | 10 | 0.604 ± 0.134 (n=79) | 0.709 ± 0.071 (n=175) | 0.391 ± 0.124 (n=145) | 0.575 ± 0.032 |
| traffic_5pct | 773 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 7 | 1.000 ± 0.000 (n=135) | 1.000 ± 0.000 (n=388) | 1.000 ± 0.000 (n=250) | 1.000 ± 0.000 |
| spike_1min | 1198 / 1200 | 0.000 ± 0.000 | 0.000 ± 0.000 | - | 0.000 ± 0.000 (n=564) | 0.000 ± 0.000 (n=368) | 0.000 ± 0.000 (n=266) | 0.000 ± 0.000 |
| burst_like_6 | 397 / 1200 | 0.501 ± 0.100 | 0.501 ± 0.100 | 7 | 0.000 ± 0.000 (n=12) | 0.305 ± 0.139 (n=220) | 0.799 ± 0.133 (n=165) | 0.516 ± 0.102 |
| burst_like_10 | 378 / 1200 | 0.602 ± 0.127 | 0.599 ± 0.132 | 8 | 0.125 ± 0.398 (n=20) | 0.410 ± 0.164 (n=200) | 0.905 ± 0.100 (n=158) | 0.612 ± 0.133 |
| burst_like_20 | 406 / 1200 | 0.707 ± 0.062 | 0.653 ± 0.096 | 8 | 0.104 ± 0.199 (n=15) | 0.497 ± 0.178 (n=214) | 0.879 ± 0.091 (n=177) | 0.665 ± 0.102 |
| burst_like_40 | 399 / 1200 | 0.775 ± 0.029 | 0.657 ± 0.047 | 8 | 0.192 ± 0.261 (n=18) | 0.477 ± 0.071 (n=203) | 0.898 ± 0.043 (n=178) | 0.666 ± 0.057 |

###### Part D: burst-shaped faults (failure rate x4), normal

| length min | past min | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|
| 6 | 397 | 0.499 | 0.514 | 0.015 | 0.025 | 0.800 |
| 10 | 378 | 0.598 | 0.608 | 0.011 | 0.005 | 1.000 |
| 20 | 406 | 0.707 | 0.722 | 0.015 | 0.000 | - |
| 40 | 399 | 0.774 | 0.787 | 0.013 | 0.000 | - |

Pre-fix method at high (the only high fault runs; spike rows use the old spike rule):

##### Faults: adaptive history h1 l1, bursts 1/500 x8min, high sensitivity

Injections past the normal-sensitivity minimum. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | past min / injected | caught | within | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|
| slower_2x | 420 / 1200 | 0.944 ± 0.034 | 0.847 ± 0.072 | 6 | - (n=0) | 0.809 ± 0.083 (n=174) | 0.876 ± 0.080 (n=246) | 0.847 ± 0.072 |
| slower_5x | 433 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=175) | 1.000 ± 0.000 (n=258) | 1.000 ± 0.000 |
| cache_30pct | 437 / 1200 | 0.996 ± 0.008 | 0.982 ± 0.020 | 6 | - (n=0) | 0.964 ± 0.051 (n=193) | 0.994 ± 0.018 (n=244) | 0.982 ± 0.020 |
| failing_plus5 | 566 / 1200 | 0.708 ± 0.070 | 0.455 ± 0.079 | 10 | 0.062 ± 0.199 (n=8) | 0.414 ± 0.033 (n=309) | 0.515 ± 0.126 (n=249) | 0.752 ± 0.032 |
| failing_30pct | 825 / 1200 | 0.999 ± 0.004 | 0.992 ± 0.010 | 6 | 0.965 ± 0.041 (n=196) | 1.000 ± 0.000 (n=372) | 1.000 ± 0.000 (n=257) | 0.992 ± 0.010 |
| endpoint_100pct | 462 / 960 | 0.842 ± 0.080 | 0.762 ± 0.122 | 6 | 0.083 ± 0.265 (n=13) | 0.628 ± 0.146 (n=259) | 1.000 ± 0.000 (n=190) | 0.798 ± 0.108 |
| traffic_50pct | 242 / 1200 | 0.988 ± 0.013 | 0.897 ± 0.046 | 6 | - (n=0) | 0.889 ± 0.136 (n=111) | 0.904 ± 0.070 (n=131) | 0.897 ± 0.046 |
| traffic_5pct | 481 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=221) | 1.000 ± 0.000 (n=260) | 1.000 ± 0.000 |
| spike_1min | 1200 / 1200 | 0.003 ± 0.005 | 0.003 ± 0.005 | - | 0.000 ± 0.000 (n=573) | 0.002 ± 0.008 (n=392) | 0.009 ± 0.016 (n=235) | 0.003 ± 0.005 |
| burst_like_6 | 336 / 1200 | 0.035 ± 0.029 | 0.035 ± 0.029 | 6 | 0.000 ± 0.000 (n=4) | 0.044 ± 0.038 (n=173) | 0.024 ± 0.031 (n=159) | 0.316 ± 0.056 |
| burst_like_10 | 336 / 1200 | 0.092 ± 0.037 | 0.086 ± 0.042 | 10 | 0.000 (n=3) | 0.077 ± 0.036 (n=154) | 0.100 ± 0.071 (n=179) | 0.439 ± 0.038 |
| burst_like_20 | 336 / 1200 | 0.156 ± 0.082 | 0.066 ± 0.021 | 16 | 0.062 ± 0.199 (n=8) | 0.060 ± 0.051 (n=158) | 0.073 ± 0.045 (n=170) | 0.451 ± 0.062 |
| burst_like_40 | 343 / 1200 | 0.248 ± 0.071 | 0.062 ± 0.045 | 22 | 0.000 ± 0.000 (n=3) | 0.076 ± 0.051 (n=181) | 0.054 ± 0.076 (n=159) | 0.432 ± 0.159 |

###### Part D: burst-shaped faults (failure rate x4), high

| length min | past min | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|
| 6 | 336 | 0.036 | 0.318 | 0.283 | 0.637 | 0.028 |
| 10 | 336 | 0.092 | 0.440 | 0.348 | 0.491 | 0.061 |
| 20 | 336 | 0.152 | 0.527 | 0.375 | 0.158 | 0.075 |
| 40 | 343 | 0.248 | 0.586 | 0.338 | 0.012 | 0.000 |

##### Faults: adaptive history h1 l1, bursts off, high sensitivity

Injections past the normal-sensitivity minimum. *Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), settling and sweep included. Bands are the no-fault rpm at the fault.

| fault | past min / injected | caught | within | median t | within <20 | within 20-200 | within >200 | v2 method within |
|---|---|---|---|---|---|---|---|---|
| slower_2x | 415 / 1200 | 0.929 ± 0.062 | 0.810 ± 0.084 | 6 | - (n=0) | 0.798 ± 0.122 (n=173) | 0.821 ± 0.076 (n=242) | 0.810 ± 0.084 |
| slower_5x | 424 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=169) | 1.000 ± 0.000 (n=255) | 1.000 ± 0.000 |
| cache_30pct | 435 / 1200 | 0.996 ± 0.008 | 0.989 ± 0.013 | 6 | - (n=0) | 0.975 ± 0.031 (n=184) | 1.000 ± 0.000 (n=251) | 0.989 ± 0.013 |
| failing_plus5 | 570 / 1200 | 0.895 ± 0.034 | 0.819 ± 0.012 | 6 | 0.062 ± 0.199 (n=9) | 0.690 ± 0.060 (n=298) | 0.988 ± 0.014 (n=263) | 0.859 ± 0.029 |
| failing_30pct | 825 / 1200 | 0.999 ± 0.004 | 0.993 ± 0.010 | 6 | 0.970 ± 0.043 (n=200) | 1.000 ± 0.000 (n=373) | 1.000 ± 0.000 (n=252) | 0.993 ± 0.010 |
| endpoint_100pct | 459 / 960 | 0.897 ± 0.033 | 0.809 ± 0.087 | 6 | 0.167 ± 0.306 (n=13) | 0.699 ± 0.117 (n=252) | 1.000 ± 0.000 (n=194) | 0.833 ± 0.097 |
| traffic_50pct | 254 / 1200 | 0.996 ± 0.012 | 0.904 ± 0.076 | 6 | - (n=0) | 0.903 ± 0.102 (n=122) | 0.910 ± 0.095 (n=132) | 0.904 ± 0.076 |
| traffic_5pct | 477 / 1200 | 1.000 ± 0.000 | 1.000 ± 0.000 | 6 | - (n=0) | 1.000 ± 0.000 (n=234) | 1.000 ± 0.000 (n=243) | 1.000 ± 0.000 |
| spike_1min | 1200 / 1200 | 0.001 ± 0.003 | 0.001 ± 0.003 | - | 0.000 ± 0.000 (n=576) | 0.000 ± 0.000 (n=377) | 0.003 ± 0.011 (n=247) | 0.001 ± 0.003 |
| burst_like_6 | 337 / 1200 | 0.471 ± 0.049 | 0.471 ± 0.049 | 6 | 0.167 ± 2.118 (n=4) | 0.134 ± 0.081 (n=173) | 0.843 ± 0.075 (n=160) | 0.515 ± 0.029 |
| burst_like_10 | 334 / 1200 | 0.552 ± 0.138 | 0.549 ± 0.135 | 6 | 0.000 (n=3) | 0.208 ± 0.040 (n=160) | 0.876 ± 0.094 (n=171) | 0.589 ± 0.131 |
| burst_like_20 | 330 / 1200 | 0.612 ± 0.070 | 0.571 ± 0.092 | 6 | 0.000 ± 0.000 (n=6) | 0.232 ± 0.152 (n=157) | 0.913 ± 0.053 (n=167) | 0.646 ± 0.068 |
| burst_like_40 | 343 / 1200 | 0.709 ± 0.151 | 0.607 ± 0.151 | 7 | 0.000 ± 0.000 (n=3) | 0.353 ± 0.127 (n=167) | 0.871 ± 0.045 (n=173) | 0.657 ± 0.145 |

###### Part D: burst-shaped faults (failure rate x4), high

| length min | past min | caught | caught by v2 method | missed only by adaptivity | share no longer than Lb | caught when no longer than Lb |
|---|---|---|---|---|---|---|
| 6 | 337 | 0.472 | 0.516 | 0.045 | 0.000 | - |
| 10 | 334 | 0.554 | 0.596 | 0.042 | 0.000 | - |
| 20 | 330 | 0.615 | 0.673 | 0.058 | 0.000 | - |
| 40 | 343 | 0.708 | 0.738 | 0.029 | 0.000 | - |

### C. AC13

Same 6-hour outage (10:00 to 16:00) on Tuesdays 29 and 43, then on Tuesday 50, optionally 60
minutes later; 25 services per seed and kind, peaks 50 to 3,000 requests a minute.

| bursts | method | shift min | kind | judgeable / services | opened day 50 | within time to catch | median delay | excluded |
|---|---|---|---|---|---|---|---|---|
| 1/500 x8min | adaptive history h1 l1 | 0 | failing_30pct | 100 / 100 | 0.990 | 0.990 | 7 | 1.000 |
| 1/500 x8min | adaptive history h1 l1 | 0 | slower_5x | 85 / 100 | 1.000 | 0.976 | 7 | 0.999 |
| 1/500 x8min | adaptive history h1 l1 | 0 | traffic_5pct | 100 / 100 | 1.000 | 1.000 | 7 | 1.000 |
| 1/500 x8min | adaptive history h1 l1 | 60 | failing_30pct | 100 / 100 | 0.990 | 0.990 | 7 | 1.000 |
| 1/500 x8min | adaptive history h1 l1 | 60 | slower_5x | 86 / 100 | 1.000 | 1.000 | 7 | 0.998 |
| 1/500 x8min | adaptive history h1 l1 | 60 | traffic_5pct | 100 / 100 | 1.000 | 1.000 | 7 | 1.000 |
| 1/500 x8min | adaptive history-volume h1 l1 loo | 0 | failing_30pct | 100 / 100 | 0.990 | 0.990 | 7 | 1.000 |
| 1/500 x8min | adaptive history-volume h1 l1 loo | 0 | slower_5x | 85 / 100 | 1.000 | 0.976 | 7 | 0.999 |
| 1/500 x8min | adaptive history-volume h1 l1 loo | 0 | traffic_5pct | 100 / 100 | 1.000 | 1.000 | 7 | 1.000 |
| 1/500 x8min | adaptive history-volume h1 l1 loo | 60 | failing_30pct | 100 / 100 | 0.990 | 0.990 | 7 | 1.000 |
| 1/500 x8min | adaptive history-volume h1 l1 loo | 60 | slower_5x | 86 / 100 | 1.000 | 1.000 | 7 | 0.998 |
| 1/500 x8min | adaptive history-volume h1 l1 loo | 60 | traffic_5pct | 100 / 100 | 1.000 | 1.000 | 7 | 1.000 |

- The one *failing 30%* service that does not open on day 50 (seed 2, service 10002, both shifts)
  never closed its day-43 finding: the outage flagged endpoints that are judgeable only at busy
  hours, and FR11 lets a signal with too little traffic neither close nor extend a finding. It was
  still open on day 50, so the outage joined it (BR2). This is a closing-rule problem: a finding
  can stay open for days after a long outage on a quiet service. It never happened in any no-fault
  run (no finding over a day in any configuration).
- The late *slower* case is a service near slower's 200-request floor at 10:00.

### Calibration (seed 101, 40 services, normal, bursts 1/500 × 4 min)

v2's own code on the same services gave 2.79, against 2.76 for `plain` here: the port is faithful,
and this seed is heavier than seeds 1 to 4.

| method | bursts | sens | services | all | failing | ep | slower | traffic | <20 | 20-200 | >200 | worst | share with any | daily mode, days 3-20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adaptive history h0.995 l0.9 | 1/500 x4min | normal | 40 (1 seeds) | 2.70 | 2.02 | 1.08 | 0.17 | 0.00 | 0.00 | 0.60 | 5.37 | 8.50 | 0.55 | 2.23 |
| adaptive history h0.999 l0.9 | 1/500 x4min | normal | 40 (1 seeds) | 2.06 | 1.14 | 1.15 | 0.17 | 0.00 | 0.00 | 0.50 | 4.08 | 6.75 | 0.55 | 1.72 |
| adaptive history h0.999 l1 | 1/500 x4min | normal | 40 (1 seeds) | 2.02 | 1.12 | 1.13 | 0.17 | 0.00 | 0.00 | 0.50 | 4.00 | 6.75 | 0.55 | 1.70 |
| adaptive history h0.9995 l0.95 | 1/500 x4min | normal | 40 (1 seeds) | 1.61 | 0.71 | 0.97 | 0.17 | 0.00 | 0.00 | 0.45 | 3.16 | 6.00 | 0.55 | 1.52 |
| adaptive history h1 l1 | 1/500 x4min | normal | 40 (1 seeds) | 0.81 | 0.20 | 0.48 | 0.17 | 0.00 | 0.00 | 0.15 | 1.62 | 3.75 | 0.55 | 1.17 |
| adaptive short-in h0.999 l0.9 | 1/500 x4min | normal | 40 (1 seeds) | 2.47 | 1.69 | 1.12 | 0.17 | 0.00 | 0.00 | 0.60 | 4.88 | 6.75 | 0.57 | 1.95 |
| adaptive spec h0.999 l0.9 | 1/500 x4min | normal | 40 (1 seeds) | 2.72 | 2.01 | 1.13 | 0.17 | 0.00 | 0.00 | 0.62 | 5.39 | 8.50 | 0.57 | 2.19 |
| plain | 1/500 x4min | normal | 40 (1 seeds) | 2.76 | 2.07 | 1.12 | 0.17 | 0.00 | 0.00 | 0.62 | 5.49 | 8.50 | 0.57 | 2.27 |

### Limitations

- Synthetic traffic. The burst rate, length and height (×4) are guesses until the demo is measured
  (D15); the conclusions move with them, which is why the sweep spans them.
- No deploys, daylight-saving changes, late data, or user actions; no small-sample p95 bias.
- The final candidate's faults were run at normal only; its high and low rows are no-fault only.
- Lb has no record for learning days 0 to 2. H's like-volume window is a factor of two around a
  power-of-two level.
- *Traffic to 50%* sits exactly on normal's minimum, so roughly half its injections are "past the
  minimum" by noise alone; AC36's eligibility needs a margin for such faults.

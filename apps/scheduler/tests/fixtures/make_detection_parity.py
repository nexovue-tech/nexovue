# mypy: ignore-errors
# Runs in the simulation's own uv project, whose numpy and scipy the workspace does not have.
"""Writes `detection_parity.json`: the simulation's judgements, for the scheduler's
parity tests (plan T2c.2, T3.1).

The scheduler cannot import the simulation, so its verdicts are frozen here once.
Run from the repository root:

    uv run --project tools/detection-sim python \\
        apps/scheduler/tests/fixtures/make_detection_parity.py

- `windows`: windows the simulation judged, with the baseline summary it judged
  them by, and its verdict. Busy services, with a 2x slowdown, a cache-miss fault
  and a one-minute 10x spike injected, at every sensitivity, in weekly and daily
  mode and while learning. `test_detection_method.py` judges each again.
- `signals`: *failing more* and *losing traffic* windows of a calm and a bursty
  service with a +5-point failure, a 30% failure, one endpoint failing
  completely, traffic at 5% and a stop injected, judged a whole day at a time
  after the days before were judged, so the burst profile has history. Daily
  mode on day 10 and weekly on day 30.
- `baselines`: a quiet service's minute history over the days its pools select,
  late in the evening so the pools are cut at the day's end, and the summary the
  simulation built from it at each window length, for all three signals.
  `test_detection_baselines.py` builds it again.
- `profiles`: two days of the bursty service's minutes, the normal each window was
  judged by and whether it passed the base test, and the burst profile the
  simulation took from them.

Simulation minute t is `EPOCH + t` minutes in UTC; day 0 is a Monday.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "tools/detection-sim")
import detect_sim as sim

OUT = Path(__file__).with_name("detection_parity.json")
EPOCH = "2026-01-05T00:00:00+00:00"
METHOD = sim.Method(True, 1.0, 1.0, "history-volume", True, True, 1 / 7, False, True)
DAY = sim.DAY
# (seed, service): busy enough for 200 requests a window.
BUSY = ((11, 0), (12, 0))
QUIET = (21, 0)
# (seed, service, bursts, lowest and highest peak requests a minute): a calm service,
# one whose history is bursty (D18) with 14 endpoints, and a busy one whose 3
# endpoints are bursty in their own right.
FAILING = (
    (11, 0, "off", 150, 300),
    (31, 0, "0.002:8", 150, 300),
    (41, 0, "0.002:8", 800, 2000),
)
# Faults on the judged day, by start minute: (name, start, length).
FAILING_FAULTS = (
    ("failing_plus5", 600, 20),
    ("failing_30pct", 780, 20),
    ("endpoint_100pct", 960, 20),
    ("traffic_5pct", 1080, 20),
    ("stop", 1200, 20),
)
# Daily mode before 21 days, where only a stop is judged for traffic; then weekly.
FAILING_DAYS = (10, 30)
# The rows judged around each fault: its onset, and its end.
AROUND = (*range(-1, 10), *range(19, 28))


def sparse(buckets) -> list[int]:
    """Non-zero buckets as [index, count, index, count, …]."""
    return [int(v) for j in np.flatnonzero(buckets) for v in (j, buckets[j])]


def with_faults(svc, seed: int, i: int, day: int):
    """2x slower for 20 minutes from 10:00, cache misses at 30% from 13:00, and a
    10x spike at 16:00 with normal minutes around it."""
    rng = np.random.default_rng([seed, i, 9])
    req_e, err_e, hist = svc.req_e.copy(), svc.err_e.copy(), svc.hist.copy()
    ds = day * DAY
    for name, start, length in (
        ("slower_2x", 600, 20),
        ("cache_30pct", 780, 20),
        ("spike_1min", 960, 1),
    ):
        fs, fe = ds + start, ds + start + length
        r, e, h = sim.apply_fault(rng, svc, name, fs, fe, fs, fe)
        req_e[:, fs:fe], err_e[:, fs:fe], hist[fs:fe] = r, e, h
    return dataclasses.replace(svc, req_e=req_e, err_e=err_e, hist=hist)


def judged_context(svc, sens: sim.Sens, day: int) -> sim.Ctx:
    ctx = sim.Ctx(svc, sens, METHOD)
    for d in range(day):
        ctx.commit(d * DAY, (d + 1) * DAY, np.ones(DAY, bool))
    return ctx


def history_context(svc, sens: sim.Sens, day: int, normals: dict | None = None) -> sim.Ctx:
    """Every day before `day` judged in turn, as `run_timeline` does, so the record of
    windows above the base test, which the burst profile reads, exists. Nothing is
    kept out: the scheduler's tests pass their exclusions explicitly. `normals`,
    when given, collects each judged minute's normal rate and wander."""
    ctx = sim.Ctx(svc, sens, METHOD)
    for d in range(sim.LEARNING_DAYS):
        ctx.commit(d * DAY, (d + 1) * DAY, np.ones(DAY, bool))
    for d in range(sim.LEARNING_DAYS, day):
        ds = d * DAY
        j = sim.judge(ctx, ctx.cur, np.arange(ds, ds + DAY), ds)
        ctx.above[ds : ds + DAY] = j.fail.tail
        ctx.above_e[:, ds : ds + DAY] = j.ep.tail
        if normals is not None:
            for row in range(DAY):
                normals[ds + row] = (float(j.fail.p0[row]), float(j.fail.rho[row]))
        ctx.commit(ds, ds + DAY, np.ones(DAY, bool))
    return ctx


def with_failing_faults(svc, seed: int, i: int, day: int):
    """`FAILING_FAULTS` on the judged day; the endpoint fault on the least busy endpoint."""
    rng = np.random.default_rng([seed, i, 19])
    req_e, err_e, hist = svc.req_e.copy(), svc.err_e.copy(), svc.hist.copy()
    ds = day * DAY
    for name, start, length in FAILING_FAULTS:
        fs, fe = ds + start, ds + start + length
        if name == "stop":
            req_e[:, fs:fe], err_e[:, fs:fe], hist[fs:fe] = 0, 0, 0
            continue
        ep = svc.n_endpoints - 1 if name == "endpoint_100pct" else None
        r, e, h = sim.apply_fault(rng, svc, name, fs, fe, fs, fe, ep)
        req_e[:, fs:fe], err_e[:, fs:fe], hist[fs:fe] = r, e, h
    return dataclasses.replace(svc, req_e=req_e, err_e=err_e, hist=hist)


def traffic_pool(ctx: sim.Ctx, pools: sim.Pools, row: int) -> tuple[float | None, float]:
    """`judge_traffic`'s median and spread for one row, as the hourly job will hold them."""
    if pools.mode[row] == 0:
        return None, 1.0
    off = np.arange(-sim.POOL_HALF, sim.POOL_HALF + 1)
    idx = pools.centre[row][:, None] + off[None, :]
    val = (
        pools.sel[row][:, None] & (idx >= pools.lo[row][:, None]) & (idx <= pools.hi[row][:, None])
    )
    idxc = np.clip(idx, 0, sim.T - 1)
    val &= ctx.mask[idxc]
    v = np.sort(ctx.xa[idxc][val])
    if not len(v):
        return None, 1.0
    md = 0.5 * (v[(len(v) - 1) // 2] + v[len(v) // 2])
    dev = np.sort(np.abs(v - md))
    mad = 0.5 * (dev[(len(dev) - 1) // 2] + dev[len(dev) // 2])
    return float(md), float(max(1.4826 * mad, 1.0))


def failing_summary(ctx: sim.Ctx, pools: sim.Pools, j: sim.Judgement, row: int) -> dict:
    """What `judge_failing` and `judge_endpoints` judged row by: the endpoints' normals
    only for those judged, which are all that decide alpha / m."""
    n_pool, e_pool = pools.total(ctx.cs["n"])[row], pools.total(ctx.cs["e"])[row]
    assert (e_pool + 0.5) / (n_pool + 250.0) == j.fail.p0[row]
    prof = sim.profile_service(ctx, pools, row, float(j.fail.n[row]))
    level = round(np.log2(max(float(j.fail.n[row]), 1.0)))
    ep_n = pools.total_rows(ctx.ce["n"])[:, row]
    ep_e = pools.total_rows(ctx.ce["e"])[:, row]
    return {
        "mode": ("learning", "daily", "weekly")[int(pools.mode[row])],
        "days": int(pools.sel[row].sum()),
        "pool_requests": round(float(n_pool)),
        "pool_failures": round(float(e_pool)),
        "wander": float(j.fail.rho[row]),
        "profile": {
            "heights": {} if not np.isfinite(prof.height) else {str(level): prof.height},
            "minutes": int(prof.length),
            "excursions": int(prof.excursions),
            "days": float(prof.days),
        },
        "endpoints": {
            f"ep{k}": [
                round(float(ep_n[k])),
                round(float(ep_e[k])),
                float(j.ep.rho[k, row]),
                profile_record(
                    sim.profile_endpoint(ctx, pools, k, row, float(j.ep.n[k, row])),
                    float(j.ep.n[k, row]),
                ),
            ]
            for k in range(len(ep_n))
            if ep_n[k] > 0 and j.ep.judged[k, row]
        },
    }


def profile_record(prof, requests: float) -> dict:
    """A profile as the scheduler holds it: its height only at the window's level."""
    level = round(np.log2(max(requests, 1.0)))
    return {
        "heights": {} if not np.isfinite(prof.height) else {str(level): prof.height},
        "minutes": int(prof.length),
        "excursions": int(prof.excursions),
        "days": float(prof.days),
    }


def signal_cases() -> list[dict]:
    """Failing and traffic windows, judged a whole day at once against judged history."""
    cases = []
    rows = sorted({start + step for _, start, _ in FAILING_FAULTS for step in AROUND})
    for seed, i, bursts, low, high in FAILING:
        base = sim.make_service(seed, i, sim.Bursts.parse(bursts), low, high)
        for day in FAILING_DAYS:
            svc = with_failing_faults(base, seed, i, day)
            ds = day * DAY
            needed: set[int] = set()
            failing_rows, traffic_rows = [], []
            for sens in sim.SENS.values():
                ctx = history_context(svc, sens, day)
                j = sim.judge(ctx, ctx.cur, np.arange(ds, ds + DAY), ds)
                runs = sim.trailing_runs(j.fail.tail, np.asarray(sim.run_ending(ctx.above, ds)))
                ep_before = sim.run_ending(ctx.above_e, ds)
                ep_runs = sim.trailing_runs(j.ep.tail, ep_before)
                for row in rows:
                    m = ds + row
                    needed.update(range(m - sens.w + 1, m + 1))
                    if j.pools.mode[row] == 0:
                        continue
                    failing_rows.append(failing_row(sens, ctx, j, row, m, runs, ep_runs, ep_before))
                    traffic_rows.append(traffic_row(sens, ctx, j, row, m, day))
            cases.append(
                {
                    "service": f"{seed}:{i}:{bursts}:day{day}",
                    "minutes": {str(t): minute_record(svc, t) for t in sorted(needed)},
                    "failing": failing_rows,
                    "traffic": traffic_rows,
                }
            )
    return cases


def minute_record(svc, t: int) -> list:
    """[requests, errors, [endpoint index, requests, errors, …] for busy endpoints]."""
    eps = [
        int(v)
        for k in np.flatnonzero(svc.req_e[:, t])
        for v in (k, svc.req_e[k, t], svc.err_e[k, t])
    ]
    return [int(svc.req_e[:, t].sum()), int(svc.err_e[:, t].sum()), eps]


def failing_row(sens, ctx, j, row: int, m: int, runs, ep_runs, ep_before) -> dict:
    fail, ep = j.fail, j.ep
    judged, normal = sim.is_normal(j, "failing", row)
    endpoints = {}
    for k in range(ep.n.shape[0]):
        if not ep.judged[k, row]:
            continue
        ep_judged, ep_normal = sim.is_normal(j, ("ep", k), row)
        # [unusual, critical, normal, plain, above, run before]: numbers, to keep the
        # fixture small.
        endpoints[f"ep{k}"] = [
            int(ep.unusual[k, row]),
            int(ep.critical[k, row]),
            int(ep_judged and ep_normal),
            int(ep.plain[k, row]),
            int(ep.tail[k, row]),
            int(ep_runs[k, row - 1]) if row > 0 else int(ep_before[k]),
        ]
    return {
        "sensitivity": sens.name,
        "minute": m,
        "baseline": failing_summary(ctx, j.pools, j, row),
        "run_before": int(runs[row - 1]) if row > 0 else int(sim.run_ending(ctx.above, m)),
        "judged": bool(fail.judged[row]),
        "above": bool(fail.tail[row]),
        "plain": bool(fail.plain[row]),
        "unusual": bool(fail.unusual[row]),
        "critical": bool(fail.critical[row]),
        "normal": bool(judged and normal),
        "endpoints": endpoints,
    }


def traffic_row(sens, ctx, j, row: int, m: int, day: int) -> dict:
    t = j.traffic
    median, spread = traffic_pool(ctx, j.pools, row)
    return {
        "sensitivity": sens.name,
        "minute": m,
        "baseline": {
            "mode": ("learning", "daily", "weekly")[int(j.pools.mode[row])],
            "days": int(j.pools.sel[row].sum()),
            "median": median,
            "spread": spread,
            "stops_only": bool(j.pools.mode[row] == 1 and day < sim.WEEKLY_FROM_DAY),
        },
        "judged": bool(t.judged[row]),
        "unusual": bool(t.unusual[row]),
        "shadow": bool(t.shadow[row]),
        "critical": bool(t.critical[row]),
        "z": float(t.z[row]),
        "normal": bool(t.judged[row] and t.z[row] > -2),
    }


def profile_cases() -> list[dict]:
    """The bursty service's last two judged days before day 30, at normal sensitivity:
    each minute's counts, a busy service's most bursty endpoint's window counts and record,
    its normal, its record above the base test, and the profile taken from them."""
    seed, i, bursts, low, high = FAILING[1]
    svc = sim.make_service(seed, i, sim.Bursts.parse(bursts), low, high)
    busy = sim.make_service(*FAILING[2][:2], sim.Bursts.parse(FAILING[2][2]), *FAILING[2][3:])
    day = FAILING_DAYS[1]
    cases = []
    for sens in (sim.SENS["normal"],):
        normals: dict[int, tuple[float, float]] = {}
        ctx = history_context(svc, sens, day, normals)
        a, b = (day - 2) * DAY, day * DAY
        c = ctx.cur
        volumes = sorted({2 ** round(np.log2(float(c.n[t]))) for t in (a + 600, a + 1300)})
        expected = {}
        for volume in volumes:
            prof = sim.burst_profile(
                c.n,
                c.e,
                ctx.above,
                ctx.profile_mask,
                sim.MIN_REQ_FAIL,
                [(a, b)],
                sens.w,
                1.0,
                1.0,
                float(volume),
            )
            expected[str(round(np.log2(volume)))] = {
                "height": None if not np.isfinite(prof.height) else prof.height,
                "minutes": int(prof.length),
                "excursions": int(prof.excursions),
            }
        busy_ctx = history_context(busy, sens, day)
        bc = busy_ctx.cur
        k = int(np.argmax([busy_ctx.above_e[e, a:b].sum() for e in range(busy.n_endpoints)]))
        ep_volume = 2 ** round(np.log2(max(float(bc.n_e[k, a + 600]), 1.0)))
        ep_prof = sim.burst_profile(
            bc.n_e[k],
            bc.e_e[k],
            busy_ctx.above_e[k],
            busy_ctx.profile_mask,
            sim.EP_MIN_REQ,
            [(a, b)],
            sens.w,
            1.0,
            1.0,
            float(ep_volume),
        )
        cases.append(
            {
                "sensitivity": sens.name,
                "endpoint": {
                    "windows": [[int(bc.n_e[k, t]), int(bc.e_e[k, t])] for t in range(a, b)],
                    "above": [bool(busy_ctx.above_e[k, t]) for t in range(a, b)],
                    "level": round(np.log2(ep_volume)),
                    "height": None if not np.isfinite(ep_prof.height) else ep_prof.height,
                    "minutes": int(ep_prof.length),
                    "excursions": int(ep_prof.excursions),
                },
                "from": a,
                "to": b,
                "minutes": [[int(c.req[t]), int(c.err[t])] for t in range(a - sens.w + 1, b)],
                "normals": [list(normals[t]) for t in range(a, b)],
                "above": [bool(ctx.above[t]) for t in range(a, b)],
                "expected": expected,
            }
        )
    return cases


def summary(ctx: sim.Ctx, pools: sim.Pools, slow: sim.Slower, row: int) -> dict:
    """What `judge_slower` judged row by, as the hourly baseline will hold it."""
    ph = pools.total(ctx.ch)[row]
    n_pool = float(ph.sum())
    cb = np.cumsum(ph)
    with np.errstate(divide="ignore", invalid="ignore"):
        below = cb[:-1] / n_pool
    jstar = 1 + int(np.nanargmin(np.where(np.isfinite(below), np.abs(below - 0.95), 9.0)))
    q0 = max(n_pool - cb[jstar - 1], 0.5) / max(n_pool, 1) if n_pool > 0 else 0.05
    assert q0 == slow.q0[row], "the summary must be the one the simulation judged by"
    p95 = sim.hist_p95(ph)
    return {
        "mode": ("learning", "daily", "weekly")[int(pools.mode[row])],
        "days": int(pools.sel[row].sum()),
        "pool_requests": round(n_pool),
        "threshold_index": jstar,
        "share_above": float(q0),
        "wander": float(slow.rho[row]),
        "normal_p95_ms": None if np.isnan(p95) else float(p95),
    }


def judge_rows(ctx: sim.Ctx, ms: np.ndarray, ds: int) -> tuple[sim.Pools, sim.Judgement]:
    """`sim.judge`'s pools and windows, with *slower* alone judged."""
    cur = ctx.cur
    pools = sim.Pools(ctx, ms, ds)
    li = ms - cur.l0
    idxw = li[:, None] - np.arange(ctx.s.w)[None, ::-1]
    now = sim.Now(
        li=li,
        idxw=idxw,
        rq=cur.req[idxw].astype(np.float64),
        er=cur.err[idxw].astype(np.float64),
        n=cur.n[li].astype(np.float64),
        e=cur.e[li].astype(np.float64),
    )
    return pools, sim.judge_slower(ctx, cur, pools, now)


def window_cases() -> list[dict]:
    """One case per service and day: its minutes once, and each sensitivity's verdicts."""
    cases = []
    rows_of_day = [*range(598, 612), *range(618, 634), *range(778, 790), *range(958, 966)]
    for seed, i in BUSY:
        base = sim.make_service(seed, i, sim.Bursts(0.0, 0.0), 150, 300)
        for day in (1, 4, 30):  # learning, daily mode, weekly mode
            svc = with_faults(base, seed, i, day)
            ds = day * DAY
            # Learning judges nothing: a few of its rows are enough.
            ms = np.array([ds + r for r in (rows_of_day[:4] if day == 1 else rows_of_day)])
            needed: set[int] = set()
            judgements = []
            for sens in sim.SENS.values():
                ctx = judged_context(svc, sens, day)
                pools, slow = judge_rows(ctx, ms, ds)
                fake = sim.Judgement(pools.mode, pools, None, None, slow, None)
                for row, m in enumerate(ms.tolist()):
                    needed.update(range(m - sens.w + 1, m + 1))
                    judged, normal = sim.is_normal(fake, "slower", row)
                    judgements.append(
                        {
                            "sensitivity": sens.name,
                            "minute": m,
                            "baseline": summary(ctx, pools, slow, row),
                            "judged": bool(slow.judged[row]),
                            "unusual": bool(slow.unusual[row]),
                            "critical": bool(slow.critical[row]),
                            "normal": bool(judged and normal),
                            "above_threshold": int(slow.x[row]),
                        }
                    )
            cases.append(
                {
                    "service": f"{seed}:{i}",
                    "minutes": {
                        str(t): [
                            int(svc.req_e[:, t].sum()),
                            int(svc.err_e[:, t].sum()),
                            sparse(svc.hist[t]),
                        ]
                        for t in sorted(needed)
                    },
                    "judgements": judgements,
                }
            )
    return cases


def baseline_cases() -> list[dict]:
    """The pools the hourly job must rebuild from history alone."""
    seed, i = QUIET
    svc = sim.make_service(seed, i, sim.Bursts(0.0, 0.0), 25, 40)
    cases = []
    for day in (4, 30):  # Friday, daily mode; Wednesday, weekly mode
        ds = day * DAY
        ms = np.arange(ds + 1335, ds + 1340)  # 22:15 to 22:19: the pools reach past midnight
        needed: set[int] = set()
        expected = {}
        for sens in sim.SENS.values():
            ctx = judged_context(svc, sens, day)
            pools, slow = judge_rows(ctx, ms, ds)
            j = sim.judge(ctx, ctx.cur, ms, ds)
            expected[sens.w] = [
                {
                    **summary(ctx, pools, slow, row),
                    "failing": failing_pools(ctx, j, row),
                    "traffic": dict(
                        zip(("median", "spread"), traffic_pool(ctx, j.pools, row), strict=True)
                    ),
                }
                for row in range(len(ms))
            ]
            for row in range(len(ms)):
                for a, b in pools.wide_ranges(row):
                    needed.update(range(max(a - 9, 0), b))
        history = [
            [
                t,
                int(svc.req_e[:, t].sum()),
                int(svc.err_e[:, t].sum()),
                sparse(svc.hist[t]),
                minute_record(svc, t)[2],
            ]
            for t in sorted(needed)
            if svc.req_e[:, t].sum() > 0
        ]
        cases.append(
            {
                "minutes": ms.tolist(),
                "history": history,
                "expected": {str(w): v for w, v in expected.items()},
            }
        )
    return cases


def failing_pools(ctx: sim.Ctx, j: sim.Judgement, row: int) -> dict:
    """The failing normal's pools, without the burst profile."""
    pools = j.pools
    ep_n = pools.total_rows(ctx.ce["n"])[:, row]
    ep_e = pools.total_rows(ctx.ce["e"])[:, row]
    return {
        "pool_requests": round(float(pools.total(ctx.cs["n"])[row])),
        "pool_failures": round(float(pools.total(ctx.cs["e"])[row])),
        "wander": float(j.fail.rho[row]),
        "endpoints": {
            f"ep{k}": [round(float(ep_n[k])), round(float(ep_e[k])), float(j.ep.rho[k, row])]
            for k in range(len(ep_n))
            if ep_n[k] > 0
        },
    }


def main() -> None:
    OUT.write_text(
        json.dumps(
            {
                "epoch": EPOCH,
                "generator": "uv run --project tools/detection-sim python "
                "apps/scheduler/tests/fixtures/make_detection_parity.py",
                "windows": window_cases(),
                "signals": signal_cases(),
                "baselines": baseline_cases(),
                "profiles": profile_cases(),
            },
            separators=(",", ":"),
        )
        + "\n"
    )


if __name__ == "__main__":
    main()

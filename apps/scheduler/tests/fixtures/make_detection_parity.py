# mypy: ignore-errors
# Runs in the simulation's own uv project, whose numpy and scipy the workspace does not have.
"""Writes `detection_parity.json`: the simulation's *slower* judgements, for the
scheduler's parity tests (plan T2c.2).

The scheduler cannot import the simulation, so its verdicts are frozen here once.
Run from the repository root:

    uv run --project tools/detection-sim python \\
        apps/scheduler/tests/fixtures/make_detection_parity.py

- `windows`: windows the simulation judged, with the baseline summary it judged
  them by, and its verdict. Busy services, with a 2x slowdown, a cache-miss fault
  and a one-minute 10x spike injected, at every sensitivity, in weekly and daily
  mode and while learning. `test_detection_method.py` judges each again.
- `baselines`: a quiet service's minute history over the days its pools select,
  late in the evening so the pools are cut at the day's end, and the summary the
  simulation built from it at each window length. `test_detection_baselines.py`
  builds it again.

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
            expected[sens.w] = [summary(ctx, pools, slow, row) for row in range(len(ms))]
            for row in range(len(ms)):
                for a, b in pools.wide_ranges(row):
                    needed.update(range(max(a - 9, 0), b))
        history = [
            [t, int(svc.req_e[:, t].sum()), int(svc.err_e[:, t].sum()), sparse(svc.hist[t])]
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


def main() -> None:
    OUT.write_text(
        json.dumps(
            {
                "epoch": EPOCH,
                "generator": "uv run --project tools/detection-sim python "
                "apps/scheduler/tests/fixtures/make_detection_parity.py",
                "windows": window_cases(),
                "baselines": baseline_cases(),
            },
            separators=(",", ":"),
        )
        + "\n"
    )


if __name__ == "__main__":
    main()

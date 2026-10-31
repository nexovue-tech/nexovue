"""Simulate F6 anomaly detection on synthetic services: false findings, caught faults and AC13.

Generates services minute by minute for 8 weeks (day 0 is a Monday, local time is simulation
time): endpoints with Zipf traffic shares, per-endpoint failure rates that drift, service-wide
failure bursts, and per-request latency kept as a 64-bucket log-spaced histogram per minute.
It runs the detector day by day from day 3, with findings and FR6's traffic shadow kept out of later
pools, counts false findings, then scores injected faults against the same detector state.

The method under test: *failing more* learns each service's own burstiness (a burst height H and
a burst length Lb from its own history) and is unusual only above H or longer than Lb; *losing
traffic* falls back to the daily pool once 21 days exist (AC13). `--method plain` runs v2's test
and `--profile spec --no-loo-height --hq 0.999 --lq 0.9` the burst profile as first specified.

Seeded and reproducible: service i of seed S draws from np.random.default_rng([S, i, stream]).

    uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py run --seed 1 \\
        --svc 0:100 --bursts 0.002:4 --faults normal,high --out out/s1.jsonl
    uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py ac13 --seed 1 \\
        --services 25 --shift 60 --out out/ac13_s1.jsonl
    uv run --project tools/detection-sim python tools/detection-sim/detect_sim.py summary \\
        out/*.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from scipy.signal import lfilter
from scipy.special import ndtr
from scipy.stats import betabinom, binom
from scipy.stats import t as student_t

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]
Counts = npt.NDArray[np.int32]
Bools = npt.NDArray[np.bool_]
Rng = np.random.Generator
# A signal of a finding: "traffic", "failing", "slower", or ("ep", endpoint index) for FR2.
Key = str | tuple[str, int]

DAY: Final = 1440
NDAYS: Final = 56  # 4 history weeks, then 4 judged weeks
T: Final = NDAYS * DAY
LEARNING_DAYS: Final = 3
WEEKLY_FROM_DAY: Final = 21  # FR5 and FR6: the weekly pattern exists from 3 weeks
JUDGED_DAYS: Final = range(28, 56)
DAILY_DAYS: Final = range(3, 21)
NB: Final = 64
LOG_EDGES: Final = np.log(
    np.geomspace(1.0, 60000.0, NB + 1)
)  # ms; outliers fold into the end buckets
K_SIGMA: Final = 4
ALPHA: Final = 0.5 * math.erfc(K_SIGMA / math.sqrt(2))  # Phi(-4), about 3.2e-5
NORMAL_P: Final = 0.05  # FR13: a signal reads normal when its tail probability is above this
RECOVERY: Final = 10
MOST: Final = 0.8
MIN_REQ_SLOWER: Final = 200
MIN_REQ_FAIL: Final = 20
EP_MIN_REQ: Final = 20
EP_MIN_FAIL: Final = 10
SETTLE: Final = 2
SWEEP: Final = 1
FAULT_LEN: Final = 60
POOL_HALF: Final = 15  # the normal pools minutes M +- 15 on each selected day
DISP_HALF: Final = 180  # dispersion and burst profile pool M +- 180: +-15 holds too few bursts
# Below this many sigmas the exact tail is far above alpha, so it is not computed.
PRESCREEN_Z: Final = 1.5
# A burst height needs this many judgeable pool windows; fewer leaves only the length rule's floor.
PROFILE_MIN_WINDOWS: Final = 60
BURST_FACTOR: Final = 4.0
# A finding that closed within this many minutes of opening was unusual for at most D16's longest
# time to catch (20 min) before its 10 recovery minutes: a short excursion, not an outage.
SHORT_FINDING: Final = 30
# D18 decided 1 excursion a day; 1 a week is the false-alarm budget itself, and a history that
# reaches it would break the budget under v2's test, so tolerance starts there.
BURSTY_PER_DAY: Final = 1 / 7
UNJUDGED_CLOSE: Final = 360  # D20: 6 hours
CLEAR_MARGIN: Final = 1.1  # D19: a fault counts for AC36 at 110% of the minimum change
DEFAULT_HQ: Final = 1.0  # the highest window in 4 weeks: below it sits inside the bursts
DEFAULT_LQ: Final = 1.0  # 4 weeks hold about 5 excursions above the threshold
DEFAULT_PROFILE: Final = "history-volume"
DEFAULT_LOO_HEIGHT: Final = True
FAIL_DRIFT_SD: Final = 0.35
CACHE_SD: Final = 0.3
BUSY_RPM: Final = 200  # D16: time to catch is 12 minutes at or above this, 20 below
CATCH_BUSY: Final = 12
CATCH_QUIET: Final = 20
CRIT_SLOWER: Final = 4.0
CRIT_FAIL: Final = 0.20
CRIT_TRAFFIC: Final = 0.10
BANDS: Final = ("<20", "20-200", ">200")


@dataclass(frozen=True, slots=True)
class Sens:
    name: str
    w: int
    slower: float
    pts: float
    fmin: int
    ratio: float

    @property
    def emin(self) -> float:
        """Expected window count at which a fall to `ratio` reaches z = -4 on the Anscombe scale."""
        return (K_SIGMA / (2 * (1 - math.sqrt(self.ratio)))) ** 2

    @property
    def n80(self) -> int:
        return math.ceil(MOST * self.w)


SENS: Final = {
    "normal": Sens("normal", 5, 1.5, 0.02, 5, 0.50),
    "high": Sens("high", 3, 1.25, 0.01, 5, 0.70),
    "low": Sens("low", 10, 2.0, 0.05, 10, 0.30),
}


@dataclass(frozen=True, slots=True)
class Bursts:
    rate: float  # chance per minute that a burst starts; 0 turns bursts off
    mean_len: float  # minutes, geometric

    @property
    def label(self) -> str:
        return "off" if self.rate == 0 else f"1/{round(1 / self.rate)} x{self.mean_len:g}min"

    @classmethod
    def parse(cls, text: str) -> Bursts:
        """`off`, or `rate:mean_len` such as `0.002:4`."""
        if text == "off":
            return cls(0.0, 0.0)
        rate, length = text.split(":")
        return cls(float(rate), float(length))


@dataclass(frozen=True, slots=True)
class Method:
    adaptive: bool  # False runs v2's failing test, with no burst profile
    hq: float  # burst height quantile
    lq: float  # burst length quantile
    # Where the burst profile comes from: "spec" is the +-180 pool with findings left out;
    # "short-in" keeps the minutes of short findings, without which a service that alerts on every
    # burst never sees one; "history" also widens it to the last 28 days, since +-180 minutes on
    # 4 days holds about one burst above the threshold; "history-volume" takes the height only from
    # windows of like volume, since quiet windows' rates are noisier. The normal excludes findings.
    profile: str
    # Judge the height without the window's worst minute, as the tail test is (FR12): otherwise
    # one bad minute inside a tolerated burst lifts it above H.
    loo_height: bool
    # D18: tolerate bursts only on a service (or endpoint) whose history shows at least one
    # excursion per judged day; others use v2's test.
    switch: bool
    bursty_per_day: float  # D18's threshold: excursions per judged day
    # No tolerated burst may outlast D16's time to catch, so Lb is capped below it.
    lb_cap: bool
    # D20: a signal unjudged for UNJUDGED_CLOSE minutes stops holding its finding open.
    close_unjudged: bool

    @property
    def whole_days(self) -> bool:
        return self.profile.startswith("history")

    @property
    def short_in(self) -> bool:
        return self.profile != "spec"

    @property
    def label(self) -> str:
        if not self.adaptive:
            return "plain"
        loo = " loo" if self.loo_height else ""
        switch = f" switch{self.bursty_per_day:g}" if self.switch else ""
        close = " d20" if self.close_unjudged else ""
        cap = " lbcap" if self.lb_cap else ""
        tail = f"{switch}{cap}{close}"
        return f"adaptive {self.profile} h{self.hq:g} l{self.lq:g}{loo}{tail}"


@dataclass(frozen=True, slots=True)
class Profile:
    """A service's (or endpoint's) own bursts, from its pool: the height and length it tolerates."""

    height: float
    length: float
    windows: int
    excursions: int
    days: float = 0.0  # judged days of history the excursions come from

    def bursty(self, per_day: float) -> bool:
        """D18: at least `per_day` excursions above the tail threshold per judged day."""
        return self.days > 0 and self.excursions / self.days >= per_day


# ------------------------------------------------------------------------------------ traffic model
def ar1(rng: Rng, shape: tuple[int, ...], sd: float, tau: float) -> Floats:
    """Stationary AR(1) along the last axis, with marginal spread `sd` and memory `tau` minutes."""
    phi = math.exp(-1.0 / tau)
    eps = rng.normal(0.0, sd * math.sqrt(1 - phi * phi), shape)
    eps[..., 0] = rng.normal(0.0, sd, shape[:-1])
    return np.asarray(lfilter([1.0], [1.0, -phi], eps, axis=-1), dtype=np.float64)


def day_shape(kind: str, minute_of_day: Ints) -> Floats:
    h = minute_of_day / 60.0
    if kind == "business":
        return 0.15 + 0.85 * np.clip(np.sin(np.pi * (h - 7) / 12), 0, None) ** 1.5
    if kind == "consumer":
        return 0.25 + 0.75 * (0.5 + 0.5 * np.cos(2 * np.pi * (h - 20) / 24)) ** 2
    return np.ones_like(h)


WEEKDAY: Final = np.array(
    [1.10, 1.00, 1.02, 0.97, 0.85, 1.00, 0.90]
)  # Mon..Sun, before weekend level


@dataclass(frozen=True, slots=True)
class Latency:
    mu: Floats  # per endpoint, log ms
    sig: Floats
    q: Floats  # cache-miss share
    logf: Floats  # log of the cache-miss slowdown


@dataclass(frozen=True, slots=True, eq=False)
class Service:
    peak: float
    kind: str
    weekend: float
    share: Floats  # (E,) traffic share per endpoint
    req_e: Counts  # (E, T)
    err_e: Counts  # (E, T)
    p_e: npt.NDArray[np.float32]  # (E, T) true failure probability
    hist: Counts  # (T, NB)
    lat: Latency
    delta: Floats  # (T,) service-wide log-latency shift
    qshare: Floats  # (T, E)
    cron: bool

    @property
    def n_endpoints(self) -> int:
        return int(self.req_e.shape[0])


def bucket_probs(mu: Floats, sig: Floats) -> Floats:
    """mu (n, E) and sig (E,) to the bucket probabilities (n, E, NB) of a log-normal in log ms."""
    z = (LOG_EDGES[None, None, 1:-1] - mu[:, :, None]) / sig[None, :, None]
    c = ndtr(z)
    n, e = mu.shape
    c = np.concatenate([np.zeros((n, e, 1)), c, np.ones((n, e, 1))], axis=2)
    return np.diff(c, axis=2)


def draw_hist(rng: Rng, req_e: Counts, lat: Latency, delta: Floats, qshare: Floats) -> Counts:
    """Per-minute latency histograms (n, NB): each minute is multinomial over its endpoint mixture.

    Weighting each endpoint's two-component log-normal by its share of that minute's requests gives
    exactly the bucket counts of requests drawn endpoint by endpoint."""
    n = req_e.shape[1]
    out = np.zeros((n, NB), np.int32)
    for a in range(0, n, DAY):
        b = min(a + DAY, n)
        ne = req_e[:, a:b].T.astype(np.float64)
        tot = ne.sum(1)
        wgt = ne / np.maximum(tot, 1)[:, None]
        mu = lat.mu[None, :] + delta[a:b, None]
        q = qshare[a:b]
        pr = (
            (wgt * (1 - q))[:, :, None] * bucket_probs(mu, lat.sig)
            + (wgt * q)[:, :, None] * bucket_probs(mu + lat.logf[None, :], lat.sig)
        ).sum(1)
        pr[tot == 0] = 1.0 / NB
        pr /= pr.sum(1, keepdims=True)
        out[a:b] = rng.multinomial(tot.astype(np.int64), pr)
    return out


def gen_service(rng: Rng, burst_rng: Rng, peak: float, bursts: Bursts) -> Service:
    """One synthetic service. Bursts draw from their own stream, so every burst setting shares the
    rest of the service."""
    t = np.arange(T)
    mod, day = t % DAY, t // DAY
    kind = str(rng.choice(["business", "consumer", "flat"], p=[0.45, 0.4, 0.15]))
    weekend = 1.0 if kind == "flat" else float(rng.choice([0.15, 0.5, 1.0]))
    wd = WEEKDAY * np.exp(rng.normal(0, 0.03, 7))
    wd[5:] *= weekend
    base = peak * day_shape(kind, mod) * wd[day % 7]
    w_short = ar1(rng, (T,), 0.07, 66)  # about an hour of level wander
    w_slow = ar1(rng, (T,), 0.05, DAY)  # a day of level drift
    lam = base * np.exp(w_short + w_slow) * rng.gamma(50, 1 / 50, T)  # 14% noise per minute
    batch = np.zeros(T, bool)
    if rng.random() < 0.2:
        at = int(rng.integers(0, DAY))
        for d in range(NDAYS):
            m = d * DAY + at + int(rng.integers(-3, 4))
            if 0 <= m < T:
                batch[m] = True
        lam[batch] *= 3
    n_ep = int(rng.integers(1, 21))
    share = 1.0 / np.arange(1, n_ep + 1) ** 1.1
    share /= share.sum()
    req_e = rng.poisson(lam[None, :] * share[:, None]).astype(np.int32)
    # Failures: per-endpoint base 0-2% (15% never fail), log-normal drift, service-wide bursts.
    pbase = np.where(rng.random(n_ep) < 0.15, 0.0, rng.uniform(0, 0.02, n_ep))
    drift = ar1(rng, (n_ep, T), FAIL_DRIFT_SD, 60)
    burst = np.zeros(T, bool)
    if bursts.rate > 0:
        starts = np.flatnonzero(burst_rng.random(T) < bursts.rate)
        lengths = burst_rng.geometric(1 / bursts.mean_len, len(starts))
        for st, ln in zip(starts, lengths, strict=True):
            burst[st : st + int(ln)] = True
    mult = np.where(burst, BURST_FACTOR, 1.0)
    p_e = np.minimum(1.0, pbase[:, None] * np.exp(drift) * mult[None, :]).astype(np.float32)
    err_e = rng.binomial(req_e, p_e).astype(np.int32)
    # Latency: per-endpoint log-normal plus a cache-miss component whose share wanders.
    lat = Latency(
        mu=math.log(rng.uniform(30, 300)) + rng.normal(0, 0.5, n_ep),
        sig=rng.uniform(0.4, 0.7, n_ep),
        q=rng.uniform(0.03, 0.07, n_ep),
        logf=np.log(rng.uniform(4, 8, n_ep)),
    )
    qmul = np.exp(ar1(rng, (T,), CACHE_SD, 60))
    qshare = np.minimum(0.5, lat.q[None, :] * qmul[:, None])
    pause = rng.random(T) < 1 / 1000
    delta = 0.15 * base / base.max() + 0.5 * w_short + math.log(4) * pause + math.log(2) * batch
    hist = draw_hist(rng, req_e, lat, delta, qshare)
    return Service(
        peak=peak,
        kind=kind,
        weekend=weekend,
        share=np.asarray(share, np.float64),
        req_e=req_e,
        err_e=err_e,
        p_e=p_e,
        hist=hist,
        lat=lat,
        delta=delta,
        qshare=qshare,
        cron=bool(batch.any()),
    )


# ------------------------------------------------------------------------------------ statistics
def bb_sf(x: Floats, n: Floats, p: Floats, rho: Floats) -> Floats:
    """P(X >= x) for X beta-binomial(n, p, intra-class correlation rho); binomial when rho is 0."""
    out = np.ones(x.shape)
    p = np.clip(p, 1e-12, 1 - 1e-12)
    pos = x > 0
    b0 = pos & (rho <= 1e-9)
    b1 = pos & (rho > 1e-9)
    if b0.any():
        out[b0] = binom.sf(x[b0] - 1, n[b0].astype(np.int64), p[b0])
    if b1.any():
        k = 1.0 / rho[b1] - 1.0
        out[b1] = betabinom.sf(x[b1] - 1, n[b1].astype(np.int64), p[b1] * k, (1 - p[b1]) * k)
    return np.clip(out, 0.0, 1.0)


def mom_rho(n: Floats, x: Floats, snn: Floats, sxx: Floats, snx: Floats) -> Floats:
    """Beta-binomial intra-class correlation by the method of moments over pool windows, floor 0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        p = x / n
        s = sxx - 2 * p * snx + p * p * snn
        den = snn - n
        rho = (s / (p * (1 - p)) - (n - snn / n)) / den
    ok = (n > 0) & (p > 0) & (p < 1) & (den > 0) & np.isfinite(rho)
    return np.where(ok, np.clip(rho, 0.0, 0.999), 0.0)


def hist_p95(h: npt.NDArray[Any]) -> Floats:
    """p95 from bucket counts (..., NB), log-linear inside the bucket; nan when empty."""
    hf = np.asarray(h, np.float64)
    total = hf.sum(-1)
    c = np.cumsum(hf, -1)
    tgt = 0.95 * total
    b = np.argmax(c >= tgt[..., None] - 1e-9, axis=-1)
    hb = np.take_along_axis(hf, b[..., None], -1)[..., 0]
    below = np.take_along_axis(c, b[..., None], -1)[..., 0] - hb
    frac = np.clip((tgt - below) / np.maximum(hb, 1e-12), 0, 1)
    lp = LOG_EDGES[b] + frac * (LOG_EDGES[b + 1] - LOG_EDGES[b])
    return np.where(total > 0, np.exp(lp), np.nan)


def sorted_median(v: Floats, cnt: Ints) -> Floats:
    """Median of each row of v whose `cnt` valid entries are sorted first, nan after."""
    lo = np.maximum((cnt - 1) // 2, 0)
    hi = np.maximum(cnt // 2, 0)
    r = np.arange(len(v))
    return np.where(cnt > 0, 0.5 * (v[r, lo] + v[r, hi]), np.nan)


def safe_div(a: Floats, b: Floats) -> Floats:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 0, a / b, 0.0)


def run_lengths(flags: Bools) -> Ints:
    """Lengths of the runs of True in a 1-D array."""
    d = np.diff(np.concatenate(([0], flags.astype(np.int8), [0])))
    return np.flatnonzero(d == -1) - np.flatnonzero(d == 1)


def trailing_runs(flags: Bools, before: Ints) -> Ints:
    """Length of the run of True ending at each position of the last axis, continuing `before`."""
    pos = np.arange(1, flags.shape[-1] + 1)
    last_false = np.maximum.accumulate(np.where(flags, 0, pos), axis=-1)
    run = pos - last_false
    return np.where(last_false == 0, run + before[..., None], run)


def run_ending(flags: Bools, end: int) -> Ints:
    """Per row, the run of True ending just before minute `end` (looking back at most two days)."""
    seg = flags[..., max(0, end - 2 * DAY) : end][..., ::-1]
    if seg.shape[-1] == 0:
        return np.zeros(flags.shape[:-1], np.int64)
    whole = seg.all(-1)
    return np.where(whole, seg.shape[-1], np.argmin(seg, axis=-1)).astype(np.int64)


def burst_profile(
    n: Counts | Ints,
    e: Counts | Ints,
    above: Bools,
    usable: Bools,
    floor: int,
    ranges: Iterable[tuple[int, int]],
    w: int,
    hq: float,
    lq: float,
    volume: float | None = None,
) -> Profile:
    """The burst height and length a pool shows, over windows ending at usable minutes in `ranges`.

    Height is the `hq` quantile of judgeable window failure rates, from windows within a factor of
    two of `volume` requests when it is given. Length is the `lq` quantile of the durations, in
    minutes, of the pool's own runs above the beta-binomial threshold, never below the sustain
    window."""
    rates: list[Floats] = []
    durations: list[Ints] = []
    for a, b in ranges:
        ok = usable[a:b] & (n[a:b] >= floor)
        if volume is not None:
            ok &= (n[a:b] >= volume / 2) & (n[a:b] <= volume * 2)
        rates.append(e[a:b][ok] / n[a:b][ok])
        durations.append(run_lengths(above[a:b] & usable[a:b]) + w - 1)
    r = np.concatenate(rates) if rates else np.zeros(0)
    d = np.concatenate(durations) if durations else np.zeros(0, np.int64)
    height = float(np.quantile(r, hq)) if len(r) >= PROFILE_MIN_WINDOWS else -math.inf
    length = max(float(w), float(np.quantile(d, lq))) if len(d) else float(w)
    return Profile(height, length, len(r), len(d))


# ------------------------------------------------------------------------------------ windows
def window_sums(x: npt.NDArray[Any], w: int, axis: int) -> Ints:
    """Sums over the `w` minutes ending at each minute, along `axis` (shorter at the start)."""
    c = np.cumsum(x, axis=axis, dtype=np.int64)
    pad = [(0, 0)] * x.ndim
    pad[axis] = (1, 0)
    c = np.pad(c, pad)
    hi = np.arange(1, x.shape[axis] + 1)
    lo = np.maximum(hi - w, 0)
    out: Ints = np.take(c, hi, axis=axis) - np.take(c, lo, axis=axis)
    return out


class Cur:
    """Window sums ending at each local minute, and the minute data, over a range starting at l0."""

    def __init__(self, req_e: Counts, err_e: Counts, hist: Counts, w: int, l0: int) -> None:
        self.l0, self.w = l0, w
        self.req_e, self.err_e, self.hist = req_e, err_e, hist
        self.req: Ints = req_e.sum(0, dtype=np.int64)
        self.err: Ints = err_e.sum(0, dtype=np.int64)
        self.n = window_sums(self.req, w, 0)
        self.e = window_sums(self.err, w, 0)
        self.n_e = window_sums(req_e, w, 1)
        self.e_e = window_sums(err_e, w, 1)
        self.h = window_sums(hist, w, 0)
        top = np.cumsum(self.h[:, ::-1], axis=1)[:, ::-1]
        # a[:, j] is the window's count in buckets j and above.
        self.a = np.concatenate([top, np.zeros((len(top), 1), np.int64)], axis=1)
        self.valid: Bools = np.arange(req_e.shape[1]) >= w - 1


class Ctx:
    """Detector state for one service at one sensitivity: usable minutes, masked cumulative sums,
    and the frozen per-minute record of which windows were above the beta-binomial threshold."""

    def __init__(self, svc: Service, s: Sens, method: Method) -> None:
        self.s, self.method = s, method
        self.cur = Cur(svc.req_e, svc.err_e, svc.hist, s.w, 0)
        c = self.cur
        n_ep = svc.n_endpoints
        self.mask = np.zeros(T, bool)
        self.profile_mask = np.zeros(T, bool)  # the mask, plus short findings when short_in
        self.profile_cache: dict[tuple[int, int, int], Profile] = {}  # (endpoint or -1, day, level)
        self.above = np.zeros(T, bool)
        self.above_e = np.zeros((n_ep, T), bool)
        self.xa = 2 * np.sqrt(c.n + 0.375)
        self.cu = np.zeros(T + 1)
        self.cs = {k: np.zeros(T + 1) for k in ("n", "e", "nn", "ee", "ne")}
        self.ce = {k: np.zeros((n_ep, T + 1)) for k in ("n", "e", "nn", "ee", "ne")}
        self.ch = np.zeros((T + 1, NB))
        self.ca2 = np.zeros((T + 1, NB + 1))
        self.cna = np.zeros((T + 1, NB + 1))

    def commit(self, ds: int, de: int, usable: Bools) -> None:
        """Fix the usable readings of [ds, de) and extend the cumulative sums over them."""
        c = self.cur
        m = usable & c.valid[ds:de]
        self.mask[ds:de] = m
        self.profile_mask[ds:de] |= m  # short findings closed today are already marked
        mf = m.astype(np.float64)

        def ext(cum: Floats, x: Floats, time_axis: int) -> None:
            if time_axis == 0:
                xm = x * (mf if x.ndim == 1 else mf[:, None])
                cum[ds + 1 : de + 1] = cum[ds] + np.cumsum(xm, axis=0)
            else:
                xm = x * mf[None, :]
                cum[:, ds + 1 : de + 1] = cum[:, ds : ds + 1] + np.cumsum(xm, axis=1)

        n, e = c.n[ds:de].astype(np.float64), c.e[ds:de].astype(np.float64)
        ext(self.cu, np.ones(de - ds), 0)
        for k, x in (("n", n), ("e", e), ("nn", n * n), ("ee", e * e), ("ne", n * e)):
            ext(self.cs[k], x, 0)
        ne, ee = c.n_e[:, ds:de].astype(np.float64), c.e_e[:, ds:de].astype(np.float64)
        for k, x in (("n", ne), ("e", ee), ("nn", ne * ne), ("ee", ee * ee), ("ne", ne * ee)):
            ext(self.ce[k], x, 1)
        a = c.a[ds:de].astype(np.float64)
        ext(self.ch, c.h[ds:de].astype(np.float64), 0)
        ext(self.ca2, a * a, 0)
        ext(self.cna, n[:, None] * a, 0)


class Pools:
    """The selected past days for each judged minute, and sums over their +-15 and +-180 ranges."""

    def __init__(self, ctx: Ctx, ms: Ints, ds: int) -> None:
        nm = len(ms)
        self.n_rows, self.ds = nm, ds
        k = np.arange(1, 29)
        c = ms[:, None] - DAY * k[None, :]
        lo = np.maximum(c - POOL_HALF, 0)
        hi = np.minimum(c + POOL_HALF, ds - 1)  # only days already finished
        ok = (c >= 0) & (hi >= lo)
        lo = np.where(ok, lo, 0)
        hi = np.where(ok, hi, -1)
        has = (ctx.cu[hi + 1] - ctx.cu[lo]) > 0
        wk = (k % 7 == 0)[None, :]
        same = ((np.maximum(c, 0) // DAY) % 7 >= 5) == ((ms[:, None] // DAY) % 7 >= 5)
        weekly = (has & wk).sum(1) >= 3
        dsel = has & same
        daily = ~weekly & (dsel.sum(1) >= 3)
        self.sel: Bools = np.where(weekly[:, None], has & wk, dsel) & (weekly | daily)[:, None]
        self.mode: Ints = np.where(weekly, 2, np.where(daily, 1, 0))
        self.centre, self.lo, self.hi = c, lo, hi
        self.pair_row, pair_k = np.nonzero(self.sel)  # sorted by row
        self.rows = np.flatnonzero(self.mode > 0)
        self.starts = np.searchsorted(self.pair_row, self.rows)
        self.seg_end = np.append(self.starts[1:], len(self.pair_row))
        self.seg_of = np.full(nm, -1)
        self.seg_of[self.rows] = np.arange(len(self.rows))
        self.lo_p, self.hi_p = lo[self.pair_row, pair_k], hi[self.pair_row, pair_k] + 1
        cp = c[self.pair_row, pair_k]
        self.lo_d = np.maximum(cp - DISP_HALF, 0)
        self.hi_d = np.maximum(np.minimum(cp + DISP_HALF, ds - 1), self.lo_d - 1) + 1

    def _reduce(self, vals: Floats, axis: int, shape: tuple[int, ...]) -> Floats:
        out = np.zeros(shape)
        if len(self.rows):
            r = np.add.reduceat(vals, self.starts, axis=axis)
            if axis == 0:
                out[self.rows] = r
            else:
                out[:, self.rows] = r
        return out

    def _bounds(self, wide: bool) -> tuple[Ints, Ints]:
        return (self.lo_d, self.hi_d) if wide else (self.lo_p, self.hi_p)

    def total(self, cum: Floats, wide: bool = False) -> Floats:
        """Pool sums of a cumulative sum over time on axis 0: (rows,) or (rows, K)."""
        lo, hi = self._bounds(wide)
        return self._reduce(cum[hi] - cum[lo], 0, (self.n_rows, *cum.shape[1:]))

    def total_col(self, cum: Floats, cols: Ints, wide: bool = False) -> Floats:
        lo, hi = self._bounds(wide)
        jj = cols[self.pair_row]
        return self._reduce(cum[hi, jj] - cum[lo, jj], 0, (self.n_rows,))

    def total_rows(self, cum: Floats, wide: bool = False) -> Floats:
        """Pool sums of a per-endpoint cumulative sum (E, T + 1): (E, rows)."""
        lo, hi = self._bounds(wide)
        return self._reduce(cum[:, hi] - cum[:, lo], 1, (cum.shape[0], self.n_rows))

    def wide_ranges(self, row: int) -> list[tuple[int, int]]:
        """The +-180 minute ranges [a, b) of one judged minute's selected days."""
        g = int(self.seg_of[row])
        if g < 0:
            return []
        a, b = int(self.starts[g]), int(self.seg_end[g])
        return list(zip(self.lo_d[a:b].tolist(), self.hi_d[a:b].tolist(), strict=True))


# ------------------------------------------------------------------------------------ judging
@dataclass(slots=True)
class Now:
    """The windows being judged: their sums and their minutes."""

    li: Ints  # local index of each window's last minute
    idxw: Ints  # (rows, w) local minutes of each window
    rq: Floats  # (rows, w) requests per minute
    er: Floats
    n: Floats
    e: Floats


@dataclass(slots=True)
class Failing:
    judged: Bools
    n: Floats
    e: Floats
    p0: Floats
    rho: Floats
    tail: Bools  # beta-binomial tail test with and without the worst minute
    plain: Bools  # unusual under the v2 method
    unusual: Bools  # unusual under the adaptive method (equals plain when not adaptive)
    critical: Bools


@dataclass(slots=True)
class Slower:
    judged: Bools
    n: Floats
    x: Floats
    q0: Floats
    rho: Floats
    unusual: Bools
    critical: Bools


@dataclass(slots=True)
class Traffic:
    judged: Bools
    unusual: Bools
    shadow: Bools  # FR6: clearly low before 21 days, kept out of the baseline, never a finding
    z: Floats
    normal: Floats
    critical: Bools


@dataclass(slots=True)
class Judgement:
    mode: Ints
    pools: Pools
    fail: Failing
    ep: Failing  # per endpoint: arrays (E, rows)
    slow: Slower
    traffic: Traffic


def tail_test(
    n: Floats,
    e: Floats,
    p0: Floats,
    rho: Floats,
    alpha: Floats,
    judged: Bools,
    forced: Bools,
    worst: Callable[[Ints], tuple[Floats, Floats]],
) -> Bools:
    """Exact beta-binomial tail below alpha, with and without the worst minute, on flat arrays.

    `worst(rows)` returns that minute's requests and failures. Rows under PRESCREEN_Z sigmas skip
    the exact tail unless `forced`, which keeps the v2 candidates exactly as v2 computed them."""
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (e - n * p0) / np.sqrt(n * p0 * (1 - p0) * (1 + (n - 1) * rho))
    rows = np.flatnonzero(judged & ((z >= PRESCREEN_Z) | forced))
    out = np.zeros(n.shape, bool)
    if len(rows):
        rows = rows[bb_sf(e[rows], n[rows], p0[rows], rho[rows]) < alpha[rows]]
    if len(rows):
        wn, we = worst(rows)
        out[rows] = bb_sf(e[rows] - we, n[rows] - wn, p0[rows], rho[rows]) < alpha[rows]
    return out


def judge_failing(ctx: Ctx, pools: Pools, now: Now, before: int) -> Failing:
    s = ctx.s
    p0 = (pools.total(ctx.cs["e"]) + 0.5) / (pools.total(ctx.cs["n"]) + 250.0)
    rho = mom_rho(*(pools.total(ctx.cs[k], wide=True) for k in ("n", "e", "nn", "ee", "ne")))
    n, e = now.n, now.e
    judged = (pools.mode > 0) & (n >= MIN_REQ_FAIL)
    rate = safe_div(e, n)
    most = ((now.rq > 0) & (safe_div(now.er, now.rq) >= p0[:, None] + s.pts)).sum(1) >= s.n80
    cand = judged & (e >= s.fmin) & (rate - p0 >= s.pts) & most

    def worst(rows: Ints) -> tuple[Floats, Floats]:
        wi = now.er[rows].argmax(1)
        return now.rq[rows, wi], now.er[rows, wi]

    alpha = np.full(n.shape, ALPHA)
    tail = tail_test(n, e, p0, rho, alpha, judged, cand, worst)
    plain = cand & tail
    unusual = plain.copy()
    if ctx.method.adaptive and plain.any():
        dur = trailing_runs(tail, np.asarray(before)) + s.w - 1
        rows = np.flatnonzero(plain)
        height = rate[rows]
        if ctx.method.loo_height:
            wn, we = worst(rows)
            height = safe_div(e[rows] - we, n[rows] - wn)
        for r, h in zip(rows.tolist(), height.tolist(), strict=True):
            prof = profile_service(ctx, pools, r, float(n[r]))
            if ctx.method.switch and not prof.bursty(ctx.method.bursty_per_day):
                continue  # not bursty: v2's test stands
            unusual[r] = h > prof.height or dur[r] > burst_length(ctx, prof, float(n[r]))
    return Failing(judged, n, e, p0, rho, tail, plain, unusual, rate >= CRIT_FAIL)


def burst_length(ctx: Ctx, prof: Profile, n_service: float) -> float:
    """Lb, capped when asked so that an excursion one minute longer is still caught within D16's
    time to catch for the service's current traffic (settling and the sweep included)."""
    if not ctx.method.lb_cap:
        return prof.length
    w = ctx.s.w
    cap = time_to_catch(n_service / w) - SETTLE - SWEEP - 1
    return min(prof.length, float(max(cap, w)))


def profile_ranges(ctx: Ctx, pools: Pools, row: int) -> list[tuple[int, int]]:
    if ctx.method.whole_days:
        return [(max(0, pools.ds - 28 * DAY), pools.ds)]
    return pools.wide_ranges(row)


def profile_service(ctx: Ctx, pools: Pools, row: int, n_now: float) -> Profile:
    return cached_profile(ctx, pools, row, -1, n_now)


def profile_endpoint(ctx: Ctx, pools: Pools, k: int, row: int, n_now: float) -> Profile:
    return cached_profile(ctx, pools, row, k, n_now)


def cached_profile(ctx: Ctx, pools: Pools, row: int, k: int, n_now: float) -> Profile:
    """The burst profile of the service (k = -1) or endpoint k. A whole-days profile is the same
    for the whole day, so it is computed once a day (per power-of-two volume, when by volume)."""
    by_volume = ctx.method.profile == "history-volume"
    level = round(math.log2(max(n_now, 1.0))) if by_volume else 0
    key = (k, pools.ds, level)
    if ctx.method.whole_days and key in ctx.profile_cache:
        return ctx.profile_cache[key]
    c = ctx.cur
    n, e, above = (c.n, c.e, ctx.above) if k < 0 else (c.n_e[k], c.e_e[k], ctx.above_e[k])
    prof = burst_profile(
        n,
        e,
        above,
        ctx.profile_mask,
        MIN_REQ_FAIL if k < 0 else EP_MIN_REQ,
        profile_ranges(ctx, pools, row),
        ctx.s.w,
        ctx.method.hq,
        ctx.method.lq,
        float(2**level) if by_volume else None,
    )
    if ctx.method.whole_days:
        prof = replace(prof, days=judged_days(pools.ds))
        ctx.profile_cache[key] = prof
    return prof


def judged_days(ds: int) -> float:
    """Days of the 28-day profile window that were judged (learning days hold no record)."""
    return max(0, ds - max(LEARNING_DAYS * DAY, ds - 28 * DAY)) / DAY


def judge_endpoints(ctx: Ctx, cur: Cur, pools: Pools, now: Now, before: Ints) -> Failing:
    """FR2: failing more per endpoint, at alpha / m for the m endpoints judged that minute."""
    s = ctx.s
    p0 = (pools.total_rows(ctx.ce["e"]) + 0.5) / (pools.total_rows(ctx.ce["n"]) + 250.0)
    rho = mom_rho(*(pools.total_rows(ctx.ce[k], wide=True) for k in ("n", "e", "nn", "ee", "ne")))
    n = cur.n_e[:, now.li].astype(np.float64)
    e = cur.e_e[:, now.li].astype(np.float64)
    judged = (pools.mode > 0)[None, :] & (n >= EP_MIN_REQ)
    m = np.maximum(judged.sum(0), 1)
    rate = safe_div(e, n)
    pre = judged & (e >= EP_MIN_FAIL) & (rate - p0 >= s.pts)
    cand = np.zeros(pre.shape, bool)
    ce, cm = np.nonzero(pre)
    if len(ce):
        rqe = cur.req_e[ce[:, None], now.idxw[cm]].astype(np.float64)
        ere = cur.err_e[ce[:, None], now.idxw[cm]].astype(np.float64)
        most = ((rqe > 0) & (safe_div(ere, rqe) >= p0[ce, cm][:, None] + s.pts)).sum(1) >= s.n80
        cand[ce[most], cm[most]] = True
    n_rows = n.shape[1]

    def worst(flat: Ints) -> tuple[Floats, Floats]:
        ke, km = flat // n_rows, flat % n_rows
        rq = cur.req_e[ke[:, None], now.idxw[km]].astype(np.float64)
        er = cur.err_e[ke[:, None], now.idxw[km]].astype(np.float64)
        wi = er.argmax(1)
        r = np.arange(len(flat))
        return rq[r, wi], er[r, wi]

    alpha = np.broadcast_to(ALPHA / m[None, :], n.shape).ravel()
    tail = tail_test(
        n.ravel(), e.ravel(), p0.ravel(), rho.ravel(), alpha, judged.ravel(), cand.ravel(), worst
    ).reshape(n.shape)
    plain = cand & tail
    unusual = plain.copy()
    if ctx.method.adaptive and plain.any():
        dur = trailing_runs(tail, before) + s.w - 1
        ks, rs = np.nonzero(plain)
        height = rate[ks, rs]
        if ctx.method.loo_height:
            wn, we = worst(ks * n_rows + rs)
            height = safe_div(e[ks, rs] - we, n[ks, rs] - wn)
        for k, r, h in zip(ks.tolist(), rs.tolist(), height.tolist(), strict=True):
            prof = profile_endpoint(ctx, pools, k, r, float(n[k, r]))
            if ctx.method.switch and not prof.bursty(ctx.method.bursty_per_day):
                continue
            unusual[k, r] = h > prof.height or dur[k, r] > burst_length(ctx, prof, now.n[r])
    return Failing(judged, n, e, p0, rho, tail, plain, unusual, rate >= CRIT_FAIL)


def judge_slower(ctx: Ctx, cur: Cur, pools: Pools, now: Now) -> Slower:
    """Slower: the window's count above the pool's p95 bucket, beta-binomial against the pool."""
    s, nm = ctx.s, pools.n_rows
    ph = pools.total(ctx.ch)
    n_pool = ph.sum(1)
    cb = np.cumsum(ph, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        below = cb[:, :-1] / n_pool[:, None]  # share below boundary j = 1..63
    jstar = 1 + np.nanargmin(np.where(np.isfinite(below), np.abs(below - 0.95), 9.0), axis=1)
    a_pool = n_pool - cb[np.arange(nm), jstar - 1]
    q0 = np.where(n_pool > 0, np.maximum(a_pool, 0.5) / np.maximum(n_pool, 1), 0.05)
    phd = pools.total(ctx.ch, wide=True)
    npd = phd.sum(1)
    ad = npd - np.cumsum(phd, 1)[np.arange(nm), jstar - 1]
    rho = mom_rho(
        npd,
        ad,
        pools.total(ctx.cs["nn"], wide=True),
        pools.total_col(ctx.ca2, jstar, wide=True),
        pools.total_col(ctx.cna, jstar, wide=True),
    )
    p95n = hist_p95(ph)
    x = cur.a[now.li, jstar].astype(np.float64)
    p95w = hist_p95(cur.h[now.li])
    judged = (pools.mode > 0) & (now.n >= MIN_REQ_SLOWER) & (n_pool > 0)
    unusual = np.zeros(nm, bool)
    cand = np.flatnonzero(judged & (p95w >= s.slower * p95n))
    if len(cand):
        mh = cur.hist[now.idxw[cand]]  # (k, w, NB)
        mp = hist_p95(mh)
        most = (np.nan_to_num(mp, nan=0.0) >= s.slower * p95n[cand, None]).sum(1) >= s.n80
        cand, mh = cand[most], mh[most]
        if len(cand):
            jj = jstar[cand]
            mtop = np.array([mh[i, :, jj[i] :].sum(1) for i in range(len(cand))], np.float64)
            wi = mtop.argmax(1)
            r = np.arange(len(cand))
            p1 = bb_sf(x[cand], now.n[cand], q0[cand], rho[cand])
            p2 = bb_sf(x[cand] - mtop[r, wi], now.n[cand] - now.rq[cand, wi], q0[cand], rho[cand])
            unusual[cand] = (p1 < ALPHA) & (p2 < ALPHA)
    critical = np.nan_to_num(p95w / p95n, nan=0.0) >= CRIT_SLOWER
    return Slower(judged, now.n, x, q0, rho, unusual, critical)


def judge_traffic(ctx: Ctx, pools: Pools, now: Now, ds: int) -> Traffic:
    """Losing traffic on the Anscombe scale: pool median and MAD, z <= -4 with and without the
    window's lowest minute. Judged in weekly mode, and in daily mode once 21 days exist."""
    s, w, nm = ctx.s, ctx.s.w, pools.n_rows
    med = np.full(nm, np.nan)
    sd = np.ones(nm)
    rows = pools.rows
    if len(rows):
        off = np.arange(-POOL_HALF, POOL_HALF + 1)
        idx = pools.centre[rows][:, :, None] + off[None, None, :]
        val = (
            pools.sel[rows][:, :, None]
            & (idx >= pools.lo[rows][:, :, None])
            & (idx <= pools.hi[rows][:, :, None])
        )
        idxc = np.clip(idx, 0, T - 1)
        val &= ctx.mask[idxc]
        v = np.where(val, ctx.xa[idxc], np.nan).reshape(len(rows), -1)
        cnt = val.reshape(len(rows), -1).sum(1)
        v.sort(1)
        md = sorted_median(v, cnt)
        dev = np.abs(v - md[:, None])
        dev.sort(1)
        med[rows] = md
        sd[rows] = np.maximum(1.4826 * sorted_median(dev, cnt), 1.0)
    n = now.n
    normal = np.maximum(np.nan_to_num((med / 2) ** 2 - 0.375, nan=0.0), 0.0)
    z = (2 * np.sqrt(n + 0.375) - med) / sd
    low = (n - now.rq.min(1)) * w / (w - 1)
    z_low = (2 * np.sqrt(low + 0.375) - med) / sd
    most = (now.rq <= s.ratio * normal[:, None] / w).sum(1) >= s.n80
    eligible = (pools.mode > 0) & (normal >= s.emin)
    low_now = eligible & (z <= -K_SIGMA) & (z_low <= -K_SIGMA) & (n <= s.ratio * normal) & most
    # The AC13 fix: once 21 days exist, a minute whose weekly days are excluded uses the daily pool.
    judged_mode = (pools.mode == 2) | ((pools.mode == 1) & (ds // DAY >= WEEKLY_FROM_DAY))
    return Traffic(
        judged=eligible & judged_mode,
        unusual=low_now & judged_mode,
        shadow=low_now & ~judged_mode,
        z=z,
        normal=normal,
        critical=n <= CRIT_TRAFFIC * normal,
    )


def judge(ctx: Ctx, cur: Cur, ms: Ints, ds: int) -> Judgement:
    """Judge the windows ending at global minutes `ms`, all on the day starting at `ds`, against
    pools of earlier days. Runs above the threshold continue from the frozen record before ms[0]."""
    pools = Pools(ctx, ms, ds)
    li = ms - cur.l0
    idxw = li[:, None] - np.arange(ctx.s.w)[None, ::-1]
    now = Now(
        li=li,
        idxw=idxw,
        rq=cur.req[idxw].astype(np.float64),
        er=cur.err[idxw].astype(np.float64),
        n=cur.n[li].astype(np.float64),
        e=cur.e[li].astype(np.float64),
    )
    start = int(ms[0])
    return Judgement(
        mode=pools.mode,
        pools=pools,
        fail=judge_failing(ctx, pools, now, int(run_ending(ctx.above, start))),
        ep=judge_endpoints(ctx, cur, pools, now, run_ending(ctx.above_e, start)),
        slow=judge_slower(ctx, cur, pools, now),
        traffic=judge_traffic(ctx, pools, now, ds),
    )


def is_normal(j: Judgement, key: Key, i: int) -> tuple[bool, bool]:
    """(judged, normal) for one signal at row i: FR13's normal is a tail probability above 0.05."""
    if key == "traffic":
        return bool(j.traffic.judged[i]), bool(j.traffic.z[i] > -2)
    if key == "failing":
        f = j.fail
        if not f.judged[i]:
            return False, False
        n, x, p, r = f.n[i], f.e[i], f.p0[i], f.rho[i]
    elif key == "slower":
        sl = j.slow
        if not sl.judged[i]:
            return False, False
        n, x, p, r = sl.n[i], sl.x[i], sl.q0[i], sl.rho[i]
    else:
        assert isinstance(key, tuple)
        k, ep = key[1], j.ep
        if not ep.judged[k, i]:
            return False, False
        n, x, p, r = ep.n[k, i], ep.e[k, i], ep.p0[k, i], ep.rho[k, i]
    if x <= n * p:
        return True, True
    tail = bb_sf(np.array([x]), np.array([n]), np.array([p]), np.array([r]))
    return True, bool(tail[0] > NORMAL_P)


def unusual_keys(j: Judgement, i: int) -> list[Key]:
    keys: list[Key] = [
        k
        for k, flags in (
            ("traffic", j.traffic.unusual),
            ("failing", j.fail.unusual),
            ("slower", j.slow.unusual),
        )
        if flags[i]
    ]
    keys += [("ep", int(k)) for k in np.flatnonzero(j.ep.unusual[:, i])]
    return keys


def is_critical(j: Judgement, i: int, keys: Sequence[Key]) -> bool:
    return bool(
        ("traffic" in keys and j.traffic.critical[i])
        or ("failing" in keys and j.fail.critical[i])
        or ("slower" in keys and j.slow.critical[i])
    )


def signal_name(key: Key) -> str:
    return key[0] if isinstance(key, tuple) else key


@dataclass(slots=True)
class Finding:
    open: int
    close: int | None
    first: list[str]
    crit: bool
    sigs: list[str]
    reason: str = "open"  # "recovered", or "unjudged" (D20, could no longer be judged)


@dataclass(slots=True)
class Timeline:
    findings: list[Finding]
    ctx: Ctx
    shadow_minutes: int
    excluded: Bools


def run_timeline(svc: Service, s: Sens, method: Method) -> Timeline:
    """Judge day by day; minutes inside findings and FR6's shadow stay out of later pools."""
    ctx = Ctx(svc, s, method)
    w = s.w
    for d in range(LEARNING_DAYS):  # no pool yet: learning, every reading usable
        ctx.commit(d * DAY, (d + 1) * DAY, np.ones(DAY, bool))
    findings: list[Finding] = []
    opened: dict[str, Any] | None = None
    excluded = np.zeros(T, bool)
    shadow_minutes = 0
    for d in range(LEARNING_DAYS, NDAYS):
        ds, de = d * DAY, (d + 1) * DAY
        j = judge(ctx, ctx.cur, np.arange(ds, de), ds)
        ctx.above[ds:de] = j.fail.tail
        ctx.above_e[:, ds:de] = j.ep.tail
        for sm in np.flatnonzero(j.traffic.shadow).tolist():
            excluded[max(ds, ds + sm - w + 1) : ds + sm + 1] = True
            shadow_minutes += 1
        any_unusual = j.traffic.unusual | j.fail.unusual | j.slow.unusual | j.ep.unusual.any(0)
        uidx = np.flatnonzero(any_unusual)
        i = 0 if opened is not None else (int(uidx[0]) if len(uidx) else DAY)
        while i < DAY:
            m = ds + i
            keys = unusual_keys(j, i)
            if opened is None:
                if keys:
                    opened = {
                        "open": m,
                        "start": m - w + 1,
                        "sig": dict.fromkeys(keys, 0),
                        "unjudged": dict.fromkeys(keys, 0),
                        "first": [signal_name(k) for k in keys],
                        "crit": is_critical(j, i, keys),
                    }
                    i += 1
                else:
                    nxt = uidx[uidx > i]
                    i = int(nxt[0]) if len(nxt) else DAY
                continue
            sig: dict[Key, int] = opened["sig"]
            unjudged: dict[Key, int] = opened["unjudged"]
            if keys:
                opened["crit"] |= is_critical(j, i, keys)
            for k in keys:
                sig[k] = unjudged[k] = 0
            for k in sig:
                if k in keys:
                    continue
                judged, normal = is_normal(j, k, i)
                if judged:
                    sig[k] = sig[k] + 1 if normal else 0
                    unjudged[k] = 0
                else:
                    unjudged[k] += 1
            gone = {
                k
                for k in sig
                if method.close_unjudged and sig[k] < RECOVERY and unjudged[k] >= UNJUDGED_CLOSE
            }
            if all(v >= RECOVERY or k in gone for k, v in sig.items()):
                excluded[max(opened["start"], 0) : m + 1] = True
                if method.short_in and m - opened["open"] <= SHORT_FINDING:
                    span = slice(max(opened["start"], 0), m + 1)
                    ctx.profile_mask[span] = ctx.cur.valid[span]
                findings.append(
                    Finding(
                        opened["open"],
                        m,
                        opened["first"],
                        opened["crit"],
                        sorted({signal_name(k) for k in sig}),
                        "unjudged" if gone else "recovered",
                    )
                )
                opened = None
            i += 1
        if opened is not None:
            excluded[max(opened["start"], ds) : de] = True
        ctx.commit(ds, de, ~excluded[ds:de])
    if opened is not None:
        sigs = sorted({signal_name(k) for k in opened["sig"]})
        findings.append(Finding(opened["open"], None, opened["first"], opened["crit"], sigs))
    return Timeline(findings, ctx, shadow_minutes, excluded)


# ------------------------------------------------------------------------------------ faults
BURST_LIKE: Final = (6, 10, 20, 40)  # minutes; part D, a fault shaped like the modelled bursts
FAULTS: Final = (
    "slower_2x",
    "slower_5x",
    "cache_30pct",
    "failing_plus5",
    "failing_30pct",
    "endpoint_100pct",
    "traffic_50pct",
    "traffic_5pct",
    "spike_1min",
    *(f"burst_like_{n}" for n in BURST_LIKE),
)


def target(name: str) -> str:
    if name.startswith(("slower", "cache")):
        return "slower"
    if name.startswith(("failing", "burst_like")):
        return "failing"
    if name.startswith("endpoint"):
        return "endpoint"
    if name.startswith("traffic"):
        return "traffic"
    return "any"


def fault_length(name: str) -> int:
    if name == "spike_1min":
        return 1
    if name.startswith("burst_like_"):
        return int(name.rsplit("_", 1)[1])
    return FAULT_LEN


def apply_fault(
    rng: Rng, svc: Service, name: str, a: int, b: int, fs: int, fe: int, ep: int | None = None
) -> tuple[Counts, Counts, Counts]:
    """Copies of (req_e, err_e, hist) over [a, b), with the fault applied to [fs, fe)."""
    req_e = svc.req_e[:, a:b].copy()
    err_e = svc.err_e[:, a:b].copy()
    hist = svc.hist[a:b].copy()
    sl = slice(fs - a, fe - a)
    p_e = svc.p_e[:, fs:fe].astype(np.float64)
    delta = svc.delta[fs:fe].copy()
    qsh = svc.qshare[fs:fe].copy()
    redraw = False
    if name in ("slower_2x", "slower_5x"):
        delta += math.log(2.0 if name == "slower_2x" else 5.0)
        redraw = True
    elif name == "cache_30pct":
        qsh[:] = 0.30
        redraw = True
    elif name == "failing_plus5":
        err_e[:, sl] = rng.binomial(req_e[:, sl], np.minimum(1.0, p_e + 0.05))
    elif name == "failing_30pct":
        err_e[:, sl] = rng.binomial(req_e[:, sl], np.maximum(p_e, 0.30))
    elif name.startswith("burst_like_"):
        err_e[:, sl] = rng.binomial(req_e[:, sl], np.minimum(1.0, p_e * BURST_FACTOR))
    elif name == "endpoint_100pct":
        err_e[ep, sl] = req_e[ep, sl]
    elif name.startswith("traffic"):
        frac = 0.5 if name == "traffic_50pct" else 0.05
        req_e[:, sl] = rng.binomial(req_e[:, sl], frac)
        err_e[:, sl] = rng.binomial(req_e[:, sl], p_e)
        redraw = True
    elif name == "spike_1min":
        err_e[:, sl] = req_e[:, sl]
        delta += math.log(10.0)
        redraw = True
    if redraw:
        hist[sl] = draw_hist(rng, req_e[:, sl], svc.lat, delta, qsh)
    return req_e, err_e, hist


def time_to_catch(rpm: float) -> int:
    return CATCH_BUSY if rpm >= BUSY_RPM else CATCH_QUIET


def band(rpm: float) -> str:
    return "<20" if rpm < 20 else ("20-200" if rpm < BUSY_RPM else ">200")


@dataclass(slots=True)
class FaultResult:
    fault: str
    fs: int
    rpm: float  # the no-fault requests per minute over the first fault window
    band: str
    mode: int
    eligible: bool
    past_min: bool
    caught: bool
    t: int | None
    within: bool  # caught within D16's time to catch
    caught_plain: bool  # what the v2 method (no burst profile) would have done
    within_plain: bool
    lb: float | None  # the service's burst length at the fault, part D
    h: float | None
    clear: bool = False  # D19: past the minimum by CLEAR_MARGIN, the AC36 eligibility
    bursty: bool | None = None  # D18: the service's class at the fault


def first_hit(hit: Bools, ms: Ints, fs: int) -> int | None:
    h = np.flatnonzero(hit)
    return int(ms[h[0]] - fs + 1 + SETTLE + SWEEP) if len(h) else None


def score_fault(
    rng: Rng, svc: Service, ctx: Ctx, name: str, fs: int, ep: int | None
) -> FaultResult:
    s, w = ctx.s, ctx.s.w
    fe = fs + fault_length(name)
    a, b = fs - w, fe + w + 12
    req_e, err_e, hist = apply_fault(rng, svc, name, a, b, fs, fe, ep)
    cur = Cur(req_e, err_e, hist, w, a)
    ref = Cur(svc.req_e[:, a:b], svc.err_e[:, a:b], svc.hist[a:b], w, a)
    ds = (fs // DAY) * DAY
    ms = np.arange(fs, (fs + w + 11) if name == "spike_1min" else (fe + w - 1))
    j, r = judge(ctx, cur, ms, ds), judge(ctx, ref, ms, ds)
    i0, k0 = fs + w - 1 - a, w - 1  # the first window fully inside the fault, and its row
    n_inj, n_ref = float(cur.n[i0]), float(ref.n[i0])
    rpm = n_ref / w
    mode = int(r.mode[k0])
    if name == "spike_1min":
        new = (
            (j.traffic.unusual & ~r.traffic.unusual)
            | (j.fail.unusual & ~r.fail.unusual)
            | (j.slow.unusual & ~r.slow.unusual)
            | (j.ep.unusual & ~r.ep.unusual).any(0)
        )
        # AC2 needs normal minutes around the spike: a range the reference already finds unusual
        # (a real deviation starting nearby) is not an AC2 case.
        quiet = not (
            r.traffic.unusual.any()
            or r.fail.unusual.any()
            or r.slow.unusual.any()
            or r.ep.unusual.any()
        )
        caught = bool(new.any())
        return FaultResult(
            fault=name,
            fs=fs,
            rpm=rpm,
            band=band(rpm),
            mode=mode,
            eligible=True,
            past_min=quiet,
            clear=quiet,
            caught=caught,
            t=None,
            within=caught,
            caught_plain=caught,
            within_plain=caught,
            lb=None,
            h=None,
        )
    norm = SENS["normal"]
    tgt = target(name)
    extra = float(cur.e[i0] - ref.e[i0])
    traffic_mode = mode == 2 or (mode == 1 and ds // DAY >= WEEKLY_FROM_DAY)
    if tgt == "slower":
        floor = n_inj >= MIN_REQ_SLOWER and mode > 0
        ratio = hist_p95(cur.h[i0]) / hist_p95(ref.h[i0]) if n_ref > 0 else 0.0
        ratio = float(np.nan_to_num(ratio))
        past = bool(floor and ratio >= norm.slower)
        clear = bool(floor and ratio >= 1 + CLEAR_MARGIN * (norm.slower - 1))
        hit = hit_plain = j.slow.unusual
    elif tgt == "failing":
        floor = n_inj >= MIN_REQ_FAIL and mode > 0
        past = floor and extra >= norm.fmin and extra / max(n_inj, 1) >= norm.pts
        clear = floor and failing_clear(extra, n_inj)
        hit, hit_plain = j.fail.unusual, j.fail.plain
    elif tgt == "endpoint":
        assert ep is not None
        n_ep = float(cur.n_e[ep, i0])
        floor = mode > 0 and (n_inj >= MIN_REQ_FAIL or n_ep >= EP_MIN_REQ)
        svc_path = (
            n_inj >= MIN_REQ_FAIL and extra >= norm.fmin and extra / max(n_inj, 1) >= norm.pts
        )
        ep_extra = float(cur.e_e[ep, i0] - ref.e_e[ep, i0])
        ep_path = n_ep >= EP_MIN_REQ and ep_extra >= EP_MIN_FAIL
        past = floor and (svc_path or ep_path)
        clear = floor and (
            (n_inj >= MIN_REQ_FAIL and failing_clear(extra, n_inj))
            or (n_ep >= EP_MIN_REQ and ep_extra >= math.ceil(CLEAR_MARGIN * EP_MIN_FAIL))
        )
        hit = j.fail.unusual | j.ep.unusual.any(0)
        hit_plain = j.fail.plain | j.ep.plain.any(0)
    else:
        floor = traffic_mode and float(r.traffic.normal[k0]) >= s.emin
        past = floor and n_inj <= norm.ratio * n_ref
        clear = floor and n_inj <= (1 - CLEAR_MARGIN * (1 - norm.ratio)) * n_ref
        hit = hit_plain = j.traffic.unusual
    t, t_plain = first_hit(hit, ms, fs), first_hit(hit_plain, ms, fs)
    limit = time_to_catch(rpm)
    lb = h = None
    bursty = None
    if mode > 0 and ctx.method.adaptive:
        prof = profile_service(ctx, r.pools, k0, n_ref)
        lb, h = prof.length, (prof.height if math.isfinite(prof.height) else None)
        bursty = prof.bursty(ctx.method.bursty_per_day)
    return FaultResult(
        fault=name,
        fs=fs,
        rpm=rpm,
        band=band(rpm),
        mode=mode,
        eligible=bool(floor),
        past_min=bool(past),
        caught=t is not None,
        t=t,
        within=t is not None and t <= limit,
        caught_plain=t_plain is not None,
        within_plain=t_plain is not None and t_plain <= limit,
        lb=lb,
        h=h,
        clear=bool(clear),
        bursty=bursty,
    )


def failing_clear(extra: float, n: float) -> bool:
    """D19 for failing more: at least 110% of the minimum points and of the minimum failures."""
    norm = SENS["normal"]
    return extra >= math.ceil(CLEAR_MARGIN * norm.fmin) and extra / max(n, 1) >= (
        CLEAR_MARGIN * norm.pts
    )


# ------------------------------------------------------------------------------------ commands
def make_service(seed: int, i: int, bursts: Bursts, lo_rpm: float, hi_rpm: float) -> Service:
    rng = np.random.default_rng([seed, i, 0])
    peak = float(np.exp(rng.uniform(math.log(lo_rpm), math.log(hi_rpm))))
    return gen_service(rng, np.random.default_rng([seed, i, 3]), peak, bursts)


def bursty_record(ctx: Ctx) -> dict[str, float | None]:
    """D18's class over the judged weeks: the share of days the service was bursty, and the mean
    share over endpoints that were ever judgeable."""

    def share(above: Bools, n: Ints) -> float | None:
        if not (n >= EP_MIN_REQ).any():
            return None
        days = [d * DAY for d in JUDGED_DAYS]
        hits = [
            len(run_lengths(above[ds - 28 * DAY : ds] & ctx.profile_mask[ds - 28 * DAY : ds]))
            / judged_days(ds)
            >= ctx.method.bursty_per_day
            for ds in days
        ]
        return float(np.mean(hits))

    c = ctx.cur
    eps = [share(ctx.above_e[k], c.n_e[k]) for k in range(c.n_e.shape[0])]
    known = [x for x in eps if x is not None]
    return {
        "bursty": share(ctx.above, c.n),
        "bursty_ep": float(np.mean(known)) if known else None,
    }


def method_of(a: argparse.Namespace) -> Method:
    return Method(
        a.method == "adaptive",
        a.hq,
        a.lq,
        a.profile,
        a.loo_height,
        a.switch,
        a.bursty_per_day,
        a.lb_cap,
        a.close_unjudged,
    )


def cmd_run(a: argparse.Namespace) -> None:
    bursts = Bursts.parse(a.bursts)
    method = method_of(a)
    sens = [x for x in a.sens.split(",") if x]
    fault_sens = {x for x in a.faults.split(",") if x}
    lo, hi = (int(x) for x in a.svc.split(":"))
    with Path(a.out).open("w") as fh:
        for i in range(lo, hi):
            t0 = time.time()
            svc = make_service(a.seed, i, bursts, 2, 3000)
            rec: dict[str, Any] = {
                "record": "service",
                "seed": a.seed,
                "i": i,
                "bursts": bursts.label,
                "method": method.label,
                "peak": svc.peak,
                "band": band(svc.peak),
                "E": svc.n_endpoints,
                "kind": svc.kind,
                "cron": svc.cron,
                "cfg": {},
                "faults": {},
            }
            for name in sens:
                tl = run_timeline(svc, SENS[name], method)
                rec["cfg"][name] = {
                    "findings": [
                        {
                            "day": f.open // DAY,
                            "first": f.first,
                            "crit": f.crit,
                            "sigs": f.sigs,
                            "dur": None if f.close is None else f.close - f.open,
                            "reason": f.reason,
                        }
                        for f in tl.findings
                    ],
                    "shadow_minutes": tl.shadow_minutes,
                    **bursty_record(tl.ctx),
                }
                if name in fault_sens:
                    rec["faults"][name] = [
                        asdict(x) for x in inject_faults(a, svc, tl.ctx, name, i)
                    ]
                del tl
            rec["secs"] = round(time.time() - t0, 1)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"seed {a.seed} svc {i} peak {svc.peak:.0f} {rec['secs']}s", file=sys.stderr)


def inject_faults(
    a: argparse.Namespace, svc: Service, ctx: Ctx, name: str, i: int
) -> list[FaultResult]:
    rng = np.random.default_rng([a.seed, i, 1, list(SENS).index(name)])
    epk = int(np.argmin(np.abs(svc.share - 0.05)))
    ep_ok = 0.025 <= svc.share[epk] <= 0.10  # an endpoint carrying about 5% of the traffic
    out: list[FaultResult] = []
    for fault in FAULTS:
        if fault == "endpoint_100pct" and not ep_ok:
            continue
        for _ in range(a.reps):
            d = int(rng.integers(JUDGED_DAYS.start, JUDGED_DAYS.stop))
            fs = d * DAY + int(rng.integers(60, DAY - 90))
            out.append(
                score_fault(rng, svc, ctx, fault, fs, epk if fault == "endpoint_100pct" else None)
            )
    return out


AC13_TUESDAYS: Final = (29, 36, 43, 50)  # day 0 is a Monday
AC13_OUTAGES: Final = (29, 43)
AC13_KINDS: Final = ("traffic_5pct", "failing_30pct", "slower_5x")


def cmd_ac13(a: argparse.Namespace) -> None:
    """AC13: the same 6-hour outage on 2 of the 3 past Tuesdays; the 4th Tuesday must open."""
    bursts = Bursts.parse(a.bursts)
    s = SENS["normal"]
    method = method_of(a)
    with Path(a.out).open("w") as fh:
        for kind in AC13_KINDS:
            for i in range(a.services):
                svc = make_service(a.seed, 10_000 + i, bursts, 50, 3000)
                rng = np.random.default_rng([a.seed, 10_000 + i, 2])
                days = {d: d * DAY + 600 + (a.shift if d == 50 else 0) for d in AC13_TUESDAYS}
                rpm = {d: float(svc.req_e[:, fs : fs + s.w].sum()) / s.w for d, fs in days.items()}
                for d in (*AC13_OUTAGES, 50):
                    fs, fe = days[d], days[d] + 360  # 10:00 to 16:00 local, the 4th maybe shifted
                    q, e, h = apply_fault(rng, svc, kind, fs, fe, fs, fe)
                    svc.req_e[:, fs:fe], svc.err_e[:, fs:fe], svc.hist[fs:fe] = q, e, h
                tl = run_timeline(svc, s, method)
                tgt = target(kind)
                delay: dict[int, int | None] = {}
                for d, fs in days.items():
                    hits = [
                        f.open for f in tl.findings if fs <= f.open < fs + 360 and tgt in f.sigs
                    ]
                    delay[d] = min(hits) - fs + 1 + SETTLE + SWEEP if hits else None
                rec = {
                    "record": "ac13",
                    "seed": a.seed,
                    "i": i,
                    "kind": kind,
                    "shift": a.shift,
                    "bursts": bursts.label,
                    "method": method.label,
                    "peak": svc.peak,
                    "rpm_day50": rpm[50],
                    "delay": {str(d): v for d, v in delay.items()},
                    # BR2: a finding already open on the signal when the outage starts takes it in.
                    "joined": any(
                        f.open < days[50]
                        and (f.close is None or f.close >= days[50])
                        and tgt in f.sigs
                        for f in tl.findings
                    ),
                    "excluded": {
                        str(d): float(tl.excluded[days[d] : days[d] + 360].mean())
                        for d in AC13_OUTAGES
                    },
                }
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                print(f"ac13 seed {a.seed} {kind} svc {i} delay {delay[50]}", file=sys.stderr)


# ------------------------------------------------------------------------------------ summary
Rec = dict[str, Any]


def ci(vals: Sequence[float]) -> tuple[float, float]:
    """Mean and the 95% half-width of the mean across seeds (t distribution)."""
    v = np.asarray(vals, np.float64)
    if len(v) < 2:
        return float(v.mean()), math.nan
    half = float(student_t.ppf(0.975, len(v) - 1)) * float(v.std(ddof=1)) / math.sqrt(len(v))
    return float(v.mean()), half


def fmt(vals: Sequence[float], digits: int = 2) -> str:
    if not vals:
        return "-"
    mu, hw = ci(vals)
    return f"{mu:.{digits}f}" if math.isnan(hw) else f"{mu:.{digits}f} ± {hw:.{digits}f}"


def by_seed(recs: Sequence[Rec]) -> list[list[Rec]]:
    return [[r for r in recs if r["seed"] == s] for s in sorted({r["seed"] for r in recs})]


def findings_in(r: Rec, sens: str, days: range) -> list[Rec]:
    return [f for f in r["cfg"][sens]["findings"] if f["day"] in days]


def false_rate(
    recs: Sequence[Rec], sens: str, days: range = JUDGED_DAYS, signal: str | None = None
) -> float:
    """False findings per service-week; with `signal`, only those that signal opened."""
    weeks = len(days) / 7
    return float(
        np.mean(
            [
                sum(signal is None or signal in f["first"] for f in findings_in(r, sens, days))
                / weeks
                for r in recs
            ]
        )
    )


def false_row(recs: list[Rec], sens: str) -> list[str]:
    seeds = by_seed(recs)
    cols = [fmt([false_rate(s, sens) for s in seeds])]
    cols += [fmt([false_rate(s, sens, signal=sig) for s in seeds]) for sig in SIGNALS]
    for b in BANDS:
        in_band = [[r for r in s if r["band"] == b] for s in seeds]
        cols.append(fmt([false_rate(s, sens) for s in in_band if s]))
    worst = max(len(findings_in(r, sens, JUDGED_DAYS)) / (len(JUDGED_DAYS) / 7) for r in recs)
    cols.append(f"{worst:.2f}")
    cols.append(
        fmt([float(np.mean([bool(findings_in(r, sens, JUDGED_DAYS)) for r in s])) for s in seeds])
    )
    cols.append(fmt([false_rate(s, sens, DAILY_DAYS) for s in seeds]))
    cols.append(fmt([mean_of(s, sens, "bursty") for s in seeds]))
    cols.append(fmt([mean_of(s, sens, "bursty_ep") for s in seeds]))
    weeks = len(JUDGED_DAYS) / 7
    unjudged = [
        float(
            np.mean(
                [
                    sum(f.get("reason") == "unjudged" for f in findings_in(r, sens, JUDGED_DAYS))
                    / weeks
                    for r in s
                ]
            )
        )
        for s in seeds
    ]
    cols.append(fmt(unjudged, 3))
    return cols


def mean_of(recs: Sequence[Rec], sens: str, key: str) -> float:
    vals = [r["cfg"][sens].get(key) for r in recs]
    known = [v for v in vals if v is not None]
    return float(np.mean(known)) if known else math.nan


SIGNALS: Final = ("failing", "ep", "slower", "traffic")


def summarise_false(recs: list[Rec]) -> None:
    print("### False findings per service-week (no faults), judged weeks 5 to 8\n")
    print(
        "Mean ± 95% half-width across seeds. Signal columns count the signals that opened a "
        "finding; bands are by peak rpm. *Bursty* is D18's class, as the share of judged days; "
        "*closed unjudged* is D20's closures per service-week.\n"
    )
    print(
        "| method | bursts | sens | services | all | failing | ep | slower | traffic "
        "| <20 | 20-200 | >200 | worst | share with any | daily mode, days 3-20 "
        "| bursty services | bursty endpoints | closed unjudged |"
    )
    print("|---" * 18 + "|")
    for method, bl in sorted({(r["method"], r["bursts"]) for r in recs}):
        g = [r for r in recs if r["method"] == method and r["bursts"] == bl]
        for sens in [x for x in SENS if any(x in r["cfg"] for r in g)]:
            gs = [r for r in g if sens in r["cfg"]]
            n_seeds = len(by_seed(gs))
            head = f"| {method} | {bl} | {sens} | {len(gs)} ({n_seeds} seeds) | "
            print(head + " | ".join(false_row(gs, sens)) + " |")
    print()


def injections(
    recs: Sequence[Rec],
    sens: str,
    fault: str,
    band_: str | None = None,
    rule: str = "clear",
    bursty: bool | None = None,
) -> list[Rec]:
    """Injections of `fault` that count under `rule` (D19's "clear", or the old "past_min"),
    optionally in one band or one burstiness class."""
    return [
        x
        for r in recs
        for x in r["faults"][sens]
        if x["fault"] == fault
        and x.get(rule, x["past_min"])
        and (band_ is None or x["band"] == band_)
        and (bursty is None or x.get("bursty") == bursty)
    ]


def share(
    recs: list[Rec], sens: str, fault: str, key: str, band_: str | None = None, rule: str = "clear"
) -> str:
    vals = [
        float(np.mean([x[key] for x in xs]))
        for s in by_seed(recs)
        if (xs := injections(s, sens, fault, band_, rule))
    ]
    return fmt(vals, 3)


def summarise_faults(recs: list[Rec]) -> None:
    for method, bl, sens in sorted(
        {(r["method"], r["bursts"], s) for r in recs for s in r["faults"]}
    ):
        g = [r for r in recs if r["method"] == method and r["bursts"] == bl and sens in r["faults"]]
        print(f"### Faults: {method}, bursts {bl}, {sens} sensitivity\n")
        print(
            "Injections clearly past the normal-sensitivity minimum (D19), unless marked old rule. "
            "*Within* is within D16's time to catch (12 min at 200 rpm or more, 20 below), "
            "settling and sweep included. Bands are the no-fault rpm at the fault.\n"
        )
        print(
            "| fault | clear / past min / injected | caught | within | within, old rule "
            "| median t | within <20 | within 20-200 | within >200 | v2 method within |"
        )
        print("|---" * 10 + "|")
        for fault in FAULTS:
            injected = sum(x["fault"] == fault for r in g for x in r["faults"][sens])
            if not injected:
                continue
            clear = injections(g, sens, fault)
            past = injections(g, sens, fault, rule="past_min")
            ts = [x["t"] for x in clear if x["t"] is not None]
            cols = [
                f"{len(clear)} / {len(past)} / {injected}",
                share(g, sens, fault, "caught"),
                share(g, sens, fault, "within"),
                share(g, sens, fault, "within", rule="past_min"),
                f"{np.median(ts):.0f}" if ts else "-",
                *(
                    f"{share(g, sens, fault, 'within', b)} (n={len(injections(g, sens, fault, b))})"
                    for b in BANDS
                ),
                share(g, sens, fault, "within_plain"),
            ]
            print(f"| {fault} | " + " | ".join(cols) + " |")
        print()
        summarise_burst_like(g, sens)


def summarise_burst_like(recs: list[Rec], sens: str) -> None:
    """Part D: faults shaped like the service's own bursts, which the profile may tolerate."""
    w = SENS[sens].w
    rows = []
    for n in BURST_LIKE:
        for cls in (True, False, None):
            xs = injections(recs, sens, f"burst_like_{n}", bursty=cls)
            if not xs:
                continue
            short = [x for x in xs if x["lb"] is not None and n + w - 1 <= x["lb"]]
            lost = sum(x["caught_plain"] and not x["caught"] for x in xs)
            caught_short = f"{np.mean([x['caught'] for x in short]):.3f}" if short else "-"
            name = {True: "bursty", False: "not bursty", None: "all"}[cls]
            rows.append(
                f"| {n} | {name} | {len(xs)} | {np.mean([x['caught'] for x in xs]):.3f} "
                f"| {np.mean([x['caught_plain'] for x in xs]):.3f} | {lost / len(xs):.3f} "
                f"| {len(short) / len(xs):.3f} | {caught_short} |"
            )
    if rows:
        print(f"#### Part D: burst-shaped faults (failure rate x{BURST_FACTOR:g}), {sens}\n")
        print(
            "| length min | services | clear | caught | caught by v2 method "
            "| missed only by adaptivity | share no longer than Lb "
            "| caught when no longer than Lb |"
        )
        print("|---" * 8 + "|")
        print("\n".join(rows) + "\n")


def ac13_judgeable(r: Rec) -> bool:
    """Whether the target signal is judged at normal at the 4th outage's start (its floor)."""
    n = r["rpm_day50"] * SENS["normal"].w  # the no-fault window count
    floor = {"slower": MIN_REQ_SLOWER, "failing": MIN_REQ_FAIL, "traffic": SENS["normal"].emin}
    return bool(n >= floor[target(r["kind"])])


def summarise_ac13(recs: list[Rec]) -> None:
    print("### AC13: outage on Tuesdays 29 and 43, judged on Tuesday 50 (normal)\n")
    print(
        "Shares are of services whose signal is judged at the outage's start (enough traffic). "
        "*Joined* counts outages taken in by a finding already open on the signal (BR2), which "
        "count as caught. *Excluded* is the share of the past outages' minutes kept out of the "
        "pools.\n"
    )
    print(
        "| bursts | method | shift min | kind | judgeable / services | opened or joined "
        "| within time to catch | joined | median delay | excluded |"
    )
    print("|---" * 10 + "|")
    keys = sorted({(r["bursts"], r["method"], r["shift"], r["kind"]) for r in recs})
    for bl, method, shift, kind in keys:
        every = [
            r
            for r in recs
            if (r["bursts"], r["method"], r["shift"], r["kind"]) == (bl, method, shift, kind)
        ]
        g = [r for r in every if ac13_judgeable(r)]
        if not g:
            continue
        joined = [bool(r.get("joined")) and r["delay"]["50"] is None for r in g]
        delays = [r["delay"]["50"] for r in g if r["delay"]["50"] is not None]
        opened = (len(delays) + sum(joined)) / len(g)
        within = np.mean(
            [
                j or (d is not None and d <= time_to_catch(r["rpm_day50"]))
                for r, j in zip(g, joined, strict=True)
                for d in [r["delay"]["50"]]
            ]
        )
        exc = np.mean([v for r in g for v in r["excluded"].values()])
        med = f"{np.median(delays):.0f}" if delays else "-"
        print(
            f"| {bl} | {method} | {shift} | {kind} | {len(g)} / {len(every)} | {opened:.3f} "
            f"| {within:.3f} | {sum(joined)} | {med} | {exc:.3f} |"
        )
    print()


def cmd_summary(a: argparse.Namespace) -> None:
    recs = [json.loads(line) for p in a.files for line in Path(p).read_text().splitlines() if line]
    svc = [r for r in recs if r["record"] == "service"]
    if svc:
        summarise_false(svc)
        summarise_faults([r for r in svc if r["faults"]])
    ac13 = [r for r in recs if r["record"] == "ac13"]
    if ac13:
        summarise_ac13(ac13)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def method_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--seed", type=int, required=True)
        p.add_argument(
            "--bursts", default="0.002:4", help="`off`, or start chance per minute:mean length"
        )
        p.add_argument("--method", choices=("adaptive", "plain"), default="adaptive")
        p.add_argument("--hq", type=float, default=DEFAULT_HQ, help="burst height quantile")
        p.add_argument("--lq", type=float, default=DEFAULT_LQ, help="burst length quantile")
        p.add_argument(
            "--profile",
            choices=("spec", "short-in", "history", "history-volume"),
            default=DEFAULT_PROFILE,
            help="where the burst profile comes from (see Method)",
        )
        p.add_argument(
            "--loo-height",
            action=argparse.BooleanOptionalAction,
            default=DEFAULT_LOO_HEIGHT,
            help="judge the burst height without the window's worst minute",
        )
        p.add_argument(
            "--switch",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="D18: tolerate bursts only on services and endpoints whose history is bursty",
        )
        p.add_argument(
            "--bursty-per-day",
            type=float,
            default=BURSTY_PER_DAY,
            help="D18: excursions per judged day at which a history counts as bursty",
        )
        p.add_argument(
            "--lb-cap",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="cap the burst length so a longer excursion is caught within time to catch",
        )
        p.add_argument(
            "--close-unjudged",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="D20: a signal unjudged for 6 hours stops holding its finding open",
        )
        p.add_argument("--out", required=True)

    r = sub.add_parser(
        "run", help="no-fault run, and fault scoring, for a range of services of one seed"
    )
    method_args(r)
    r.add_argument("--svc", default="0:100", help="service index range a:b")
    r.add_argument("--sens", default="normal,high,low", help="sensitivities to run")
    r.add_argument("--faults", default="", help="sensitivities that also score fault injections")
    r.add_argument("--reps", type=int, default=3, help="injections per fault per service")
    c = sub.add_parser("ac13", help="AC13 repeated-outage scenario")
    method_args(c)
    c.add_argument("--services", type=int, default=25)
    c.add_argument(
        "--shift", type=int, default=0, help="minutes the 4th Tuesday's outage starts later"
    )
    s = sub.add_parser("summary", help="print markdown tables from run and ac13 outputs")
    s.add_argument("files", nargs="+")
    a = ap.parse_args()
    {"run": cmd_run, "ac13": cmd_ac13, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    main()

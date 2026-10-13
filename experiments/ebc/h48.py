"""Gemeinsame Bibliothek fuer EBC-H4 bis H7 (Stufe A): Universen, Serien, Kalender, parallele Trade-Erzeugung, Portfolio-Helfer."""
import csv, datetime as dt, json, math, os, pickle, sys
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
import numpy as np
import sim, route_r, universe, costs, portfolio as pf

CUT = dt.date(2026, 10, 10)
START = dt.date(2024, 12, 23)
SEED = 20261011
_G = {}


def _load(iid, base):
    ts, o, h, l, c = route_r.load_daily(iid, base=base)
    ok = ts <= int(dt.datetime(CUT.year, CUT.month, CUT.day, tzinfo=dt.timezone.utc).timestamp()) * 10**9
    return ts[ok], o[ok], h[ok], l[ok], c[ok]


def setup(with_neu=True):
    rows = universe.load()
    for r in rows: r["src"] = "breit"
    base = {r["id"]: "data/ebc/daily" for r in rows}
    if with_neu and os.path.exists("results/H4/universe_neu.csv"):
        neu = universe.load("results/H4/universe_neu.csv")
        for r in neu: r["src"] = "neu"; base[r["id"]] = "data/ebc/daily_neu"
        rows = rows + neu
    if os.environ.get("EBC_MEASURED") == "1" and os.path.exists("data/ebc/spreads_valid.json"):   # gemessene Werktags-Spreads (spread_eval.py)
        sp = {k: tuple(v) for k, v in json.load(open("data/ebc/spreads_valid.json")).items()}
        ct = costs.CostTable(rows, spreads=sp, use_measured=True)
    else:
        ct = costs.CostTable(rows)
    series = {r["id"]: sim.Series(*_load(r["id"], base[r["id"]])) for r in rows}
    cal = pf.Calendar({r["id"]: series[r["id"]] for r in rows if r["src"] == "breit"})   # Kalender wie H1 (Paritaet)
    return rows, ct, series, cal


def g_start(cal):
    d = (START - dt.date(1970, 1, 1)).days
    return cal.idx[cal.roll(d)]


def atr_med(S, last=250):
    a = (S.atr / S.c * 1e4)[S.atr_ok]
    return float(np.median(a[-last:]))


def _gen(args):
    iid, arch, side_ok, opts = args
    S = _G["series"][iid]
    return (iid, arch), sim.sequential(S, arch, allow_short=side_ok, opts=opts)


def gen_trades(rows, series, archs=("T", "M", "V"), opts_by_arch=None, procs=20):
    """{(id, arch): [trade]} fuer alle Zeilen (Seitenregel H1: K2-K4 beidseitig, sonst nur long)."""
    _G["series"] = series
    jobs = [(r["id"], a, r["klasse"] in universe.SHORT_OK, (opts_by_arch or {}).get(a)) for r in rows for a in archs]
    with Pool(procs) as p:
        return dict(p.map(_gen, jobs, chunksize=16))


def build(rows, ct, series, cal, raw, g0min=0, qmax=0.10):
    """Trades mit Netto, Vektor, q-Rang; Einstieg ab g0min; Kostenfilter q <= qmax."""
    q = {r["id"]: ct.c_rt(r["id"]) / atr_med(series[r["id"]]) for r in rows}
    cls = {r["id"]: r["klasse"] for r in rows}
    src = {r["id"]: r["src"] for r in rows}
    out = []
    for (i, arch), trs in raw.items():
        if q[i] > qmax: continue
        S = series[i]
        for t in trs:
            g0, v = pf.trade_vector(S, t, cal, ct, i)
            if g0 < g0min: continue
            out.append(dict(t, id=i, arch=arch, klasse=cls[i], src=src[i], g0=g0, vec=v, rank=q[i], tie=int(i), key=(i, arch),
                            net_bps=ct.net_bps(i, t["gross_bps"], t["side"], t["hold_days"]), q=q[i], atr_bps=float(S.atr[t["signal_i"]] / S.c[t["signal_i"]] * 1e4)))
    return out, q


def summarize(r, tk, g_from, name=""):
    r = np.asarray(r)[g_from:]
    net = np.array([t["net_bps"] for t in tk]) if tk else np.zeros(1)
    return dict(arm=name, sr=pf.sharpe(r), maxdd=pf.max_dd(r), cvar5=pf.cvar5(r), ann_ret=float(r.mean() * 252),
                ann_vol=float(r.std(ddof=1) * math.sqrt(252)), n_trades=len(tk), expectancy_bps=float(net.mean()),
                hit=float((net > 0).mean()), n_ge2000=int((net >= 2000).sum()), total_ret=float(np.prod(1 + r) - 1))


def volmatch(r, ref):
    r = np.asarray(r); ref = np.asarray(ref)
    return r * (ref.std(ddof=1) / r.std(ddof=1)) if r.std(ddof=1) > 0 else r


def cluster_boot_mean(vals, months, B=5000, seed=SEED, level=0.90):
    """Cluster-Bootstrap nach Einstiegsmonat: KI des Mittelwerts."""
    vals = np.asarray(vals, float); months = np.asarray(months)
    um = np.unique(months)
    sums = np.array([vals[months == m].sum() for m in um]); cnt = np.array([(months == m).sum() for m in um])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(um), size=(B, len(um)))
    mean = sums[idx].sum(1) / cnt[idx].sum(1)
    lo, hi = np.percentile(mean, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    return float(vals.mean()), float(lo), float(hi)


def entry_month(cal, g0):
    d = dt.date(1970, 1, 1) + dt.timedelta(days=cal.days[g0])
    return d.year * 12 + d.month
